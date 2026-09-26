package com.airamatrix.capstone.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import com.airamatrix.capstone.HttpOpsReader;
import com.airamatrix.capstone.PrivateOps;
import com.airamatrix.capstone.Tools;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import software.amazon.awssdk.core.document.Document;
import software.amazon.awssdk.services.bedrockruntime.model.ContentBlock;
import software.amazon.awssdk.services.bedrockruntime.model.ConversationRole;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseOutput;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseRequest;
import software.amazon.awssdk.services.bedrockruntime.model.ConverseResponse;
import software.amazon.awssdk.services.bedrockruntime.model.Message;
import software.amazon.awssdk.services.bedrockruntime.model.StopReason;
import software.amazon.awssdk.services.bedrockruntime.model.TokenUsage;
import software.amazon.awssdk.services.bedrockruntime.model.ToolUseBlock;

/** The container, offline: Converse translation with the guardrail on every call, the HTTP contract, one full invocation. */
class RuntimeTest {
    static PrivateOps ops;

    @BeforeAll static void up() throws Exception { ops = PrivateOps.start(List.of("ACC-1001"), false); }
    @AfterAll static void down() { if (ops != null) ops.close(); }

    static final RuntimeSettings SETTINGS = RuntimeSettings.of(Map.of("GATEWAY_URL", "https://gw.example/mcp", "OAUTH_PROVIDER", "p",
            "OAUTH_SCOPES", "aira-ops/read", "MODEL_ID", "m", "GUARDRAIL_ID", "g", "GUARDRAIL_VERSION", "1"));

    @Test
    void everyConverseCallCarriesTheGuardrailAndToolsAndTranslatesBack() {
        List<ConverseRequest> seen = new ArrayList<>();
        BedrockConverseModel m = new BedrockConverseModel(r -> { seen.add(r); return toolUse("sla_report", Map.of()); }, "model-x", "gr-1", "3", null);
        ObjectNode tool = Contracts.object().put("name", "sla_report").put("description", "d");
        tool.putObject("input_schema").put("type", "object").putObject("properties");
        JsonNode out = m.messages(List.of(Map.of("role", "user", "content", "hello")), List.of(tool), "system", 100);
        assertEquals("gr-1", seen.get(0).guardrailConfig().guardrailIdentifier());
        assertEquals("3", seen.get(0).guardrailConfig().guardrailVersion());
        assertEquals("sla_report", seen.get(0).toolConfig().tools().get(0).toolSpec().name());
        assertEquals("tool_use", out.path("stop_reason").asText());
        assertEquals("tool_use", out.path("content").get(0).path("type").asText());
        assertEquals(1200, out.path("usage").path("input_tokens").asInt());
    }

    @Test
    void toolResultsAndErrorsSurviveTheTranslation() {
        ObjectNode tr = Contracts.object().put("type", "tool_result").put("tool_use_id", "t1").put("content", "{\"x\":1}").put("is_error", true);
        List<Message> msgs = BedrockConverseModel.toConverse(List.of(Map.of("role", "user", "content", Contracts.JSON.createArrayNode().add(tr))));
        assertEquals("t1", msgs.get(0).content().get(0).toolResult().toolUseId());
        assertEquals("error", msgs.get(0).content().get(0).toolResult().statusAsString());
    }

    @Test
    void anIntegralNumberFromBedrockStaysAnInteger() {
        JsonNode n = DocJson.toJson(Document.fromMap(Map.of("a", Document.fromNumber("275.0"), "b", Document.fromNumber(240),
                "c", Document.fromNumber("0.5"))));
        assertTrue(n.path("a").isIntegralNumber(), n.toString());
        assertEquals(275, n.path("a").asInt());
        assertTrue(n.path("b").isIntegralNumber());
        assertEquals(0.5, n.path("c").asDouble());
    }

    @Test
    void theContractRefusesAMissingTenantOrClock() {
        InvocationController c = new InvocationController(service(r -> { throw new AssertionError("no model call"); }));
        var bad = c.invocations("{\"prompt\": \"check\"}".getBytes(StandardCharsets.UTF_8), "s", "wat", null);
        assertEquals(400, bad.getStatusCode().value());
        assertEquals(Map.of("error", "account_id and as_of are required"), bad.getBody());
        assertEquals(400, c.invocations("not json".getBytes(StandardCharsets.UTF_8), "s", "wat", null).getStatusCode().value());
        assertEquals("Healthy", c.ping().get("status"));
    }

    @Test
    void anInvocationReturnsTheRunRecordAndItsTrace() {
        List<ConverseResponse> script = new ArrayList<>(List.of(toolUse("sla_report", Map.of()), toolUse(Tools.SUBMIT, Contracts.JSON.convertValue(noneProposal(), Map.class))));
        InvocationController c = new InvocationController(service(r -> script.remove(0)));
        var res = c.invocations(("{\"prompt\": \"check\", \"actor_id\": \"duty-manager\", \"account_id\": \"ACC-1001\", "
                + "\"as_of\": \"2026-09-24T10:30:00+05:30\"}").getBytes(StandardCharsets.UTF_8), "sess-1", "wat", null);
        assertEquals(200, res.getStatusCode().value());
        JsonNode body = Contracts.JSON.valueToTree(res.getBody());
        assertEquals("agentcore", body.path("mode").asText());
        assertEquals("blocked", body.path("status").asText(), "action none while J-5501/T-1001 are exposed: claims.omitted");
        assertTrue(body.path("verdict").path("denials").toString().contains("claims.omitted"));
        List<String> spans = new ArrayList<>();
        body.path("trace").forEach(s -> spans.add(s.path("name").asText()));
        assertTrue(spans.containsAll(List.of("run", "model.turn", "tool", "guardrail.verify")), spans.toString());
        assertFalse(body.toString().contains("wat-secret"));
    }

    @Test
    void aCachedGatewayTokenIsNeverReusedForAnotherOrAMissingWorkloadToken() {
        java.util.concurrent.atomic.AtomicInteger exchanges = new java.util.concurrent.atomic.AtomicInteger();
        IdentityTokens id = new IdentityTokens(req -> software.amazon.awssdk.services.bedrockagentcore.model.GetResourceOauth2TokenResponse
                .builder().accessToken("gw-for-" + req.workloadIdentityToken()).build(), "p", List.of("s")) {
            @Override public synchronized String gatewayToken(String wat) { exchanges.incrementAndGet(); return super.gatewayToken(wat); }
        };
        assertEquals("gw-for-caller-a", id.gatewayToken("caller-a"));
        assertEquals("gw-for-caller-a", id.gatewayToken("caller-a"), "same caller: cached");
        assertEquals("gw-for-caller-b", id.gatewayToken("caller-b"), "another caller never gets caller A's token");
        assertThrows(IllegalStateException.class, () -> id.gatewayToken(null), "no token: refused even with a warm cache");
    }

    @Test
    void noWorkloadTokenIsAClearError() {
        IdentityTokens id = new IdentityTokens((software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient) null, "p", List.of("s"));
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> id.gatewayToken(null));
        assertTrue(e.getMessage().contains("runtimeUserId"));
    }

    // ------------------------------------------------------------------ helpers

    static InvocationService service(java.util.function.Function<ConverseRequest, ConverseResponse> converse) {
        IdentityTokens identity = new IdentityTokens((software.amazon.awssdk.services.bedrockagentcore.BedrockAgentCoreClient) null, "p", List.of("s")) {
            @Override public synchronized String gatewayToken(String wat) { return "wat-secret-token"; }
        };
        return new InvocationService(SETTINGS, identity, (url, token) -> {
            HttpOpsReader h = new HttpOpsReader(ops.url, ops.readTokens.get("ACC-1001"));
            return new InvocationService.Reads() {
                @Override public JsonNode account(String id) { return h.account(id); }
                @Override public JsonNode tickets(String id) { return h.tickets(id); }
                @Override public JsonNode ticket(String id) { return h.ticket(id); }
                @Override public JsonNode jobs(String id) { return h.jobs(id); }
                @Override public JsonNode config(String k) { return h.config(k); }
                @Override public void close() { }
            };
        }, tel -> new BedrockConverseModel(converse, "m", "g", "1", tel));
    }

    static ObjectNode noneProposal() {
        ObjectNode p = Contracts.object().put("summary", "Nothing needs a customer update right now, as far as I can tell.");
        p.putArray("exposed");
        p.put("likely_cause", "");
        p.putArray("evidence").add("sla_report");
        p.putArray("untrusted_instructions_seen");
        p.set("action", Contracts.object().put("type", "none").put("reason", "nothing due"));
        return p;
    }

    static ConverseResponse toolUse(String name, Map<String, Object> input) {
        Document doc = DocJson.toDocument(input);
        return ConverseResponse.builder()
                .output(ConverseOutput.fromMessage(Message.builder().role(ConversationRole.ASSISTANT)
                        .content(ContentBlock.fromToolUse(ToolUseBlock.builder().toolUseId("tu" + name).name(name).input(doc).build())).build()))
                .stopReason(StopReason.TOOL_USE).usage(TokenUsage.builder().inputTokens(1200).outputTokens(80).totalTokens(1280).build()).build();
    }

    static { assertNotNull(SETTINGS); }
}
