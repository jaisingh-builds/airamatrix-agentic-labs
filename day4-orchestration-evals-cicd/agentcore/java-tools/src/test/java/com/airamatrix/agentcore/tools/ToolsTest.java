package com.airamatrix.agentcore.tools;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Offline: no AWS calls. Policies, environment, state handling, argument parsing and output. */
class ToolsTest {
    static final Env ENV = new Env("ap-south-1", "aira-d4", "111122223333", "global.anthropic.claude-sonnet-5");
    static final Policies.Inputs IN = new Policies.Inputs("ap-south-1", "111122223333", "aira_d4", "gr1",
            "arn:aws:bedrock-agentcore:ap-south-1:111122223333:token-vault/default/oauth2credentialprovider/aira-d4-investigator",
            "aira-d4-investigator", "arn:aws:bedrock-agentcore:ap-south-1:111122223333:memory/m-1", "aira-d4-agents-java");

    @SuppressWarnings("unchecked")
    static Map<String, Map<String, Object>> bySid(Map<String, Object> policy) {
        return ((List<Map<String, Object>>) policy.get("Statement")).stream()
                .collect(Collectors.toMap(s -> (String) s.get("Sid"), s -> s));
    }

    @Test
    void specialists_get_the_python_policy_plus_image_pull_and_nothing_more() {
        Map<String, Map<String, Object>> s = bySid(Policies.agentPolicy("investigator", IN));
        @SuppressWarnings("unchecked")
        List<String> sids = ((List<Map<String, Object>>) Policies.agentPolicy("investigator", IN).get("Statement"))
                .stream().map(m -> (String) m.get("Sid")).toList();
        assertEquals(List.of("Model", "Guardrail", "Identity", "ProviderSecret", "Logs", "Traces", "Metrics", "PullImage", "EcrAuth"), sids);
        assertEquals(9, s.size());
        assertEquals("arn:aws:ecr:ap-south-1:111122223333:repository/aira-d4-agents-java", s.get("PullImage").get("Resource"));
        assertEquals("arn:aws:bedrock:ap-south-1:111122223333:guardrail/gr1", s.get("Guardrail").get("Resource"));
        assertTrue(!s.containsKey("CallSpecialists") && !s.containsKey("Memory"));
    }

    @Test
    void only_the_supervisor_may_call_the_java_specialists_and_use_memory() {
        Map<String, Map<String, Object>> s = bySid(Policies.agentPolicy("supervisor", IN));
        assertEquals(List.of("arn:aws:bedrock-agentcore:ap-south-1:111122223333:runtime/aira_d4j_investigator-*",
                "arn:aws:bedrock-agentcore:ap-south-1:111122223333:runtime/aira_d4j_reviewer-*"), s.get("CallSpecialists").get("Resource"));
        assertEquals(IN.memoryArn(), s.get("Memory").get("Resource"));
    }

    @Test
    void trust_is_the_agentcore_service_from_this_account_only() throws Exception {
        var st = State.JSON.valueToTree(Policies.trust("111122223333")).path("Statement").path(0);
        assertEquals("bedrock-agentcore.amazonaws.com", st.path("Principal").path("Service").asText());
        assertEquals("111122223333", st.path("Condition").path("StringEquals").path("aws:SourceAccount").asText());
    }

    @Test
    void runtime_names_sit_next_to_the_python_ones() {
        assertEquals("aira_d4j_supervisor", ENV.runtimeName("supervisor"));
        assertEquals("aira-d4-agents-java", ENV.repoName());
        assertEquals("p05j_reviewer", new Env("ap-south-1", "p05", "1", "m").runtimeName("reviewer"));
    }

    static State stateWith(Path dir) throws Exception {
        Path f = dir.resolve("out/state.json");
        Files.createDirectories(f.getParent());
        Files.writeString(f, """
            {"gateway_url": "https://gw.example/mcp", "guardrail_id": "gr1", "guardrail_version": "3",
             "memory_id": "m-1", "memory_arn": "arn:m-1", "runtimes": {"supervisor": "python-arn"},
             "providers": {"investigator": {"name": "aira-d4-investigator", "arn": "arn:p-inv"},
                           "supervisor": {"name": "aira-d4-supervisor", "arn": "arn:p-sup"}},
             "clients": {"investigator": {"client_id": "c1", "scopes": ["aira-ops/read"]},
                         "supervisor": {"client_id": "c2", "scopes": ["aira-ops/read", "aira-ops/comment"]}}}""");
        return new State(f);
    }

    @Test
    void each_runtime_gets_exactly_what_the_agent_settings_read(@TempDir Path dir) throws Exception {
        Deploy d = new Deploy(ENV, stateWith(dir));
        Map<String, String> e = d.environment("supervisor", Map.of("MEMORY_ID", "m-1"));
        assertEquals("aira-d4-supervisor", e.get("OAUTH_PROVIDER"));
        assertEquals("aira-ops/read aira-ops/comment", e.get("OAUTH_SCOPES"));
        assertEquals("3", e.get("GUARDRAIL_VERSION"));
        assertEquals("m-1", e.get("MEMORY_ID"));
        assertEquals("true", e.get("AGENT_OBSERVABILITY_ENABLED"));
        assertEquals("arn:p-inv", d.policyInputs("investigator").providerArn());
    }

    @Test
    void deploy_refuses_a_non_ecr_image_and_missing_earlier_steps(@TempDir Path dir) throws Exception {
        assertThrows(IllegalArgumentException.class, () -> new Deploy(ENV, stateWith(dir)).deploy("docker.io/me/agents:1"));
        Path empty = dir.resolve("e/state.json");
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> new Deploy(ENV, new State(empty)).deploy("x.dkr.ecr.y/z:1"));
        assertTrue(e.getMessage().contains("run steps 02-05 first"));
    }

    @Test
    void saving_merges_with_other_writers_and_keeps_the_file_private(@TempDir Path dir) throws Exception {
        State a = stateWith(dir), b = new State(a.path);
        b.save("dashboard", State.JSON.getNodeFactory().textNode("aira-d4-agentcore"));   // e.g. step 9 wrote meanwhile
        a.save("java_image", State.JSON.getNodeFactory().textNode("img:1"));
        State c = new State(a.path);
        assertEquals("aira-d4-agentcore", c.get("dashboard").asText());
        assertEquals("img:1", c.get("java_image").asText());
        assertEquals("python-arn", c.get("runtimes").path("supervisor").asText());   // Python runtimes untouched
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(a.path)));
    }

    @Test
    void invoke_parses_like_invoke_py() {
        Invoke.Request r = Invoke.parse(List.of("supervisor", "Triage ticket T-1001"));
        assertEquals("ops-team", r.actor());
        assertTrue(r.session().startsWith("triage-") && r.session().length() >= 33);
        Invoke.Request f = Invoke.parse(List.of("supervisor", "What did we decide?", "--session", "triage-" + "a".repeat(32), "--actor", "p05"));
        assertEquals("p05", f.actor());
        assertThrows(IllegalArgumentException.class, () -> Invoke.parse(List.of("planner", "x")));
        assertThrows(IllegalArgumentException.class, () -> Invoke.parse(List.of("supervisor", "x", "--session", "short")));
        assertThrows(IllegalArgumentException.class, () -> Invoke.parse(List.of("supervisor")));
    }

    @Test
    void invoke_prints_what_invoke_py_prints() throws Exception {
        Invoke.Request r = new Invoke.Request("investigator", "p", "triage-" + "0".repeat(32), "ops-team");
        String out = Invoke.render(r, "{\"result\": \"cap lowered\", \"stop_reason\": \"end_turn\", \"tools_used\": [\"a\", \"b\"]}", 35);
        assertTrue(out.startsWith("  agent    investigator   session triage-"));
        assertTrue(out.contains("  tools    a, b   stop=end_turn\n\ncap lowered\n"));
        assertTrue(Invoke.render(r, "{\"error\": \"boom\"}", 1).contains("(none)   stop=None"));
    }

    @Test
    void approve_parses_like_approve_py() {
        assertEquals(new Approve.Request(16, 1, "approver"), Approve.parse(List.of("--value", "16", "--version", "1")));
        assertEquals("supervisor", Approve.parse(List.of("--as", "supervisor", "--value", "16", "--version", "1")).who());
        assertThrows(IllegalArgumentException.class, () -> Approve.parse(List.of("--value", "16")));
        assertThrows(IllegalArgumentException.class, () -> Approve.parse(List.of("--as", "root", "--value", "1", "--version", "1")));
    }

    @Test
    void approve_reads_denied_rejected_and_applied_including_sse_framing() throws Exception {
        assertEquals("DENIED   Tool Execution Denied", Approve.outcome("{\"jsonrpc\":\"2.0\",\"id\":1,\"error\":{\"message\":\"Tool Execution Denied\"}}"));
        assertEquals("REJECTED by aira-ops: 409 stale version", Approve.outcome(
                "event: message\ndata: {\"result\":{\"isError\":true,\"content\":[{\"text\":\"409 stale version\"}]}}\n\n"));
        assertEquals("APPLIED  {\"version\": 2}", Approve.outcome("{\"result\":{\"content\":[{\"text\":\"{\\\"version\\\": 2}\"}]}}"));
    }
}
