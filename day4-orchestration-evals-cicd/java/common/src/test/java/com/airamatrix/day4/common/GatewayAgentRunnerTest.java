package com.airamatrix.day4.common;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import com.airamatrix.day4.common.AgentRunner.AgentResult;
import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** The runner's controls, with a scripted model and no aira-ops (tool calls fail fast, which is fine). */
class GatewayAgentRunnerTest {

    static final String PROPOSAL = """
        {"diagnosis": "ingest.max_concurrent_jobs was lowered from 16 to 4 on 2026-09-23",
         "evidence": ["config ingest.max_concurrent_jobs: value 4, version 1"],
         "proposed_change": {"action": "none"},
         "risks": ["none"], "confidence": "medium"}""";

    /** A model that replays canned responses and records what it was sent. */
    static class Scripted implements ModelClient {
        final Deque<JsonNode> replies = new ArrayDeque<>();
        final List<List<?>> toolsSeen = new ArrayList<>();
        Scripted add(JsonNode r) { replies.add(r); return this; }
        @Override public JsonNode messages(List<?> messages, List<?> tools, String system, int maxTokens) {
            toolsSeen.add(tools);
            return replies.isEmpty() ? text("still thinking") : replies.poll();
        }
    }

    static ObjectNode reply(String stop) {
        ObjectNode r = Contracts.object();
        r.put("stop_reason", stop);
        r.putObject("usage").put("input_tokens", 1000).put("output_tokens", 200);
        r.putArray("content");
        return r;
    }

    static JsonNode text(String t) {
        ObjectNode r = reply("end_turn");
        ((ArrayNode) r.get("content")).addObject().put("type", "text").put("text", t);
        return r;
    }

    static JsonNode toolUse(String name, String inputJson) throws Exception {
        ObjectNode r = reply("tool_use");
        ((ArrayNode) r.get("content")).addObject().put("type", "tool_use").put("id", "tu_" + name + System.nanoTime())
                .put("name", name).set("input", Contracts.JSON.readTree(inputJson));
        return r;
    }

    static GatewayAgentRunner runner(ModelClient m, Map<String, String> env) {
        return new GatewayAgentRunner(m, "claude-sonnet", "http://127.0.0.1:1", "read-token", 6, 0.50, env::get);
    }

    @Test
    void a_valid_submit_is_returned_with_its_cost_and_tool_calls() throws Exception {
        Scripted m = new Scripted().add(toolUse("get_config", "{\"key\": \"ingest.max_concurrent_jobs\"}"))
                                   .add(toolUse("submit_result", PROPOSAL));
        AgentResult r = runner(m, Map.of()).run("investigate", "sys", "prompt", Contracts.PROPOSAL);
        assertEquals("none", r.output().path("proposed_change").path("action").asText());
        assertEquals(1, r.toolCalls().size());
        assertEquals(2, r.turns());
        assertTrue(r.costUsd() > 0);
        assertEquals(List.of(false), r.toolOk());      // aira-ops is unreachable here: the tool reported an error
    }

    @Test
    void the_agent_is_offered_read_tools_and_submit_only() throws Exception {
        Scripted m = new Scripted().add(toolUse("submit_result", PROPOSAL));
        runner(m, Map.of()).run("investigate", "sys", "prompt", Contracts.PROPOSAL);
        List<String> names = new ArrayList<>();
        m.toolsSeen.get(0).forEach(t -> names.add(((JsonNode) t).path("name").asText()));
        assertEquals(List.of("search_tickets", "get_ticket", "lookup_account", "get_config", "submit_result"), names);
    }

    @Test
    void a_contract_error_is_sent_back_and_a_fixed_answer_accepted() throws Exception {
        Scripted m = new Scripted().add(toolUse("submit_result", "{\"diagnosis\": \"x\"}"))
                                   .add(toolUse("submit_result", PROPOSAL));
        AgentResult r = runner(m, Map.of()).run("investigate", "sys", "prompt", Contracts.PROPOSAL);
        assertEquals(2, r.turns());
    }

    @Test
    void refuses_to_start_while_a_write_token_is_in_the_process() {
        RunnerException e = assertThrows(RunnerException.class, () ->
                runner(new Scripted(), Map.of("AIRA_OPS_APPLY_TOKEN", "write-secret"))
                        .run("investigate", "sys", "prompt", Contracts.PROPOSAL));
        assertTrue(e.getMessage().startsWith("refusing to start the investigate agent: AIRA_OPS_APPLY_TOKEN"));
    }

    @Test
    void a_text_only_agent_fails_and_reports_what_it_cost() {
        RunnerException e = assertThrows(RunnerException.class, () ->
                runner(new Scripted().add(text("done")).add(text("done")), Map.of())
                        .run("investigate", "sys", "prompt", Contracts.PROPOSAL));
        assertTrue(e.getMessage().contains("produced no result"));
        assertTrue(e.costUsd > 0);
    }

    @Test
    void max_turns_is_enforced() throws Exception {
        Scripted m = new Scripted();
        for (int i = 0; i < 10; i++) m.add(toolUse("get_ticket", "{\"id\": \"T-1001\"}"));
        RunnerException e = assertThrows(RunnerException.class, () ->
                runner(m, Map.of()).run("investigate", "sys", "prompt", Contracts.PROPOSAL));
        assertTrue(e.getMessage().contains("error_max_turns"));
    }

    @Test
    void spans_redact_and_drop_unlisted_attributes() throws Exception {
        java.nio.file.Path dir = java.nio.file.Files.createTempDirectory("spans");
        assertEquals("[REDACTED-HEX]", Spans.redact("0123456789abcdef0123456789abcdef"));
        assertEquals("Bearer [REDACTED]", Spans.redact("Bearer abcdefgh12345"));
        Map<String, Object> m = Spans.minimise(Map.of("tool", "get_ticket", "prompt", "secret stuff",
                "input", Map.of("id", "T-1001", "query", "a long free text query")));
        assertEquals("[dropped]", m.get("prompt"));
        assertEquals("[text: 22 chars]", ((Map<?, ?>) m.get("input")).get("query"));
        assertEquals("T-1001", ((Map<?, ?>) m.get("input")).get("id"));
        assertTrue(dir.toFile().exists());
    }

    /** LAB_LIVE=1 AIRA_OPS_READ_TOKEN=... : one real investigation against aira-ops. Costs ~$0.05. */
    @Test
    @EnabledIfEnvironmentVariable(named = "LAB_LIVE", matches = "1")
    void live_investigation_returns_a_valid_proposal() {
        String url = System.getenv().getOrDefault("AIRA_OPS_URL", "http://127.0.0.1:8150");
        AgentResult r = GatewayAgentRunner.fromEnv(url, System.getenv("AIRA_OPS_READ_TOKEN"), 0.40).run("investigate",
                "You investigate operations problems with read-only aira-ops tools. Propose at most one change as data.",
                "Account: ACC-1001\nReported problem: Ingest backlog on T-1001: slides queued since 06:00.\n\n"
                + "Investigate and return your proposal.", Contracts.PROPOSAL);
        System.out.println("LIVE: turns=" + r.turns() + " tools=" + r.toolCalls().stream().map(AgentRunner.ToolCall::name).toList()
                + " cost=$" + String.format("%.4f", r.costUsd()) + "\n" + r.output().toPrettyString());
        assertTrue(r.toolCalls().size() > 0);
    }
}
