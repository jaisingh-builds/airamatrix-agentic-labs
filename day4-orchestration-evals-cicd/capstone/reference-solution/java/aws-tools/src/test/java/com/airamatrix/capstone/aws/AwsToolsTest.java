package com.airamatrix.capstone.aws;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.capstone.Store;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** Offline: names, the runtime role's policy, the runtime environment, and recording an AgentCore run locally. */
class AwsToolsTest {
    @TempDir Path tmp;

    AwsEnv env() throws Exception {
        ObjectNode s = Contracts.object().put("gateway_url", "https://gw.example/mcp").put("guardrail_id", "gr123").put("guardrail_version", "2")
                .put("user_pool", "pool").put("token_url", "https://auth.example/oauth2/token");
        s.putObject("providers").putObject("investigator").put("name", "aira-d4-investigator").put("arn", "arn:aws:bedrock-agentcore:ap-south-1:000000000000:token-vault/default/oauth2credentialprovider/aira-d4-investigator");
        s.putObject("clients").putObject("investigator").put("client_id", "cid").putArray("scopes").add("aira-ops/read");
        Path shared = tmp.resolve("state.json");
        Files.writeString(shared, s.toString());
        return new AwsEnv("ap-south-1", "aira-d4", "000000000000", "model-x", shared, tmp.resolve("own.json"));
    }

    @Test
    void namesAreDistinctFromTheSharedStackAndTheOtherLanguages() throws Exception {
        AwsEnv e = env();
        assertEquals("aira_d4cap_java_responder", e.runtimeName());
        assertEquals("aira-d4-capstone-java-runtime", e.roleName());
        assertEquals("aira-d4-capstone-java", e.repoName());
    }

    @Test
    void theRuntimeRoleCanReadAndThinkButNotWriteOrCallOtherAgents() throws Exception {
        String policy = Contracts.JSON.writeValueAsString(Deploy.policy(env()));
        assertTrue(policy.contains("oauth2credentialprovider/aira-d4-investigator"), "the investigator's provider - read scope");
        assertTrue(policy.contains("guardrail/gr123"));
        assertTrue(policy.contains("repository/aira-d4-capstone-java"));
        for (String forbidden : List.of("InvokeAgentRuntime", "CreateEvent", "memory", "iam:", "secretsmanager:*", "\"*:*\"")) {
            assertFalse(policy.contains(forbidden), forbidden);
        }
    }

    @Test
    void theRuntimeEnvironmentIsExactlyWhatRuntimeSettingsReads() throws Exception {
        Map<String, String> vars = new Deploy(env()).environment();
        assertEquals("aira-d4-investigator", vars.get("OAUTH_PROVIDER"));
        assertEquals("aira-ops/read", vars.get("OAUTH_SCOPES"));
        assertEquals(List.of("AWS_REGION", "GATEWAY_URL", "OAUTH_PROVIDER", "OAUTH_SCOPES", "MODEL_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION",
                "MAX_BUDGET_USD", "MAX_TURNS", "LAB_TRACE_DIR", "AGENT_OBSERVABILITY_ENABLED"), List.copyOf(vars.keySet()));
        assertFalse(vars.values().stream().anyMatch(v -> v.toLowerCase().contains("secret") || v.toLowerCase().contains("token=")));
    }

    @Test
    void anAgentCoreRunIsStoredAndTracedLikeALocalOne() throws Exception {
        ObjectNode r = Contracts.object().put("run_id", "abcdef0123").put("status", "awaiting_approval").put("mode", "agentcore")
                .put("account_id", "ACC-1001").put("as_of", "2026-09-24T10:30:00+05:30").put("cost_usd", 0.05).put("turns", 3).put("tool_calls", 2);
        ObjectNode p = r.putObject("proposal");
        p.putObject("action").put("type", "post_customer_update").put("ticket_id", "T-1001").put("comment", "x".repeat(50)).put("reason", "why");
        r.putObject("verdict").put("passed", true).putArray("denials");
        r.putArray("trajectory");
        r.putArray("trace").addObject().put("trace_id", "abcdef0123").put("span_id", "s1").putNull("parent_id").put("name", "run")
                .put("start", 1.0).put("duration_ms", 5).put("status", "ok").putNull("error").putObject("attrs");
        try (Store s = new Store(tmp.resolve("r.sqlite").toString())) {
            Path trace = Invoke.record(s, r, "check");
            assertEquals("agentcore", s.run("abcdef0123").mode());
            assertEquals("awaiting_approval", s.run("abcdef0123").status());
            JsonNode line = Contracts.JSON.readTree(Files.readAllLines(trace).get(0));
            assertEquals("run", line.path("name").asText());
        }
    }
}
