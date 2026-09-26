package com.airamatrix.day4.lab51;

import static com.airamatrix.day4.lab51.FakeRunner.APPROVE;
import static com.airamatrix.day4.lab51.FakeRunner.BLOCK;
import static com.airamatrix.day4.lab51.FakeRunner.GOOD;
import static com.airamatrix.day4.lab51.FakeRunner.REVISE;
import static com.airamatrix.day4.lab51.FakeRunner.goodWith;
import static com.airamatrix.day4.lab51.FakeRunner.json;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.airamatrix.day4.common.AiraOpsTools;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.GatewayAgentRunner;
import com.airamatrix.day4.common.ModelClient;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * Lab 5.1 tests - every control, no model, no network. A fake runner plays the two agents; a
 * local stub plays aira-ops' write API. Same test names as lab5-1-handoff/test_pipeline.py.
 */
class PipelineTest {

    @TempDir Path tmp;
    Store store;
    Pipeline pipeline;
    AiraOpsStub ops;

    @BeforeEach
    void setUp() throws Exception {
        store = new Store(tmp.resolve("runs-" + UUID.randomUUID() + ".sqlite").toString());
        pipeline = new Pipeline(store, Duration.ofSeconds(5));
        ops = new AiraOpsStub();
    }

    @AfterEach
    void tearDown() {
        ops.close();
        store.close();
    }

    String newRun(FakeRunner runner) {
        String rid = store.createRun("ACC-1001", "Ingest backlog on T-1001");
        pipeline.advance(runner, rid);
        return rid;
    }

    static FakeRunner runner(String investigate, String review) {
        FakeRunner r = new FakeRunner().on("investigate", investigate);
        return review == null ? r : r.on("review", review);
    }

    // --- the gate
    @Test
    void test_pipeline_stops_at_the_gate_and_nothing_is_written() {
        String rid = newRun(runner(GOOD, APPROVE));
        assertEquals("awaiting_approval", store.run(rid).status());
        assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
        assertEquals(1, ops.config.get("ingest.max_concurrent_jobs").version());
        assertEquals(0, ops.writesApplied.get());
    }

    @Test
    void test_approved_change_is_applied_once_by_the_apply_identity() {
        String change = goodWith("{\"action\": \"update_config\", \"key\": \"alerts.ingest_latency_minutes\", \"value\": 20, \"expected_version\": 1}");
        String rid = newRun(runner(change, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "latency alert is too noisy", false);
        assertEquals("applied", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        assertEquals(20, ops.config.get("alerts.ingest_latency_minutes").value().asInt());
        assertEquals(List.of("pipeline-apply"), ops.auditActors);
        pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url);             // again: nothing more happens
        assertEquals(2, ops.config.get("alerts.ingest_latency_minutes").version());
        assertEquals(1, ops.writesApplied.get());
    }

    @Test
    void test_the_gate_trusts_the_decision_record_not_the_status_field() {
        String rid = newRun(runner(GOOD, APPROVE));
        store.setStatus(rid, "approved");                                    // someone edits the status by hand
        assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
        assertEquals(0, ops.writesApplied.get());
    }

    @Test
    void test_decision_needs_a_name_and_a_reason_and_happens_once() {
        String rid = newRun(runner(GOOD, APPROVE));
        for (String[] wh : new String[][] {{"", "ok"}, {"Jai", " "}}) {
            assertThrows(Pipeline.GateError.class, () -> pipeline.decide(rid, "approve", wh[0], wh[1], false));
        }
        pipeline.decide(rid, "reject", "Jai", "wait for the memory fix", false);
        assertThrows(Pipeline.GateError.class, () -> pipeline.decide(rid, "approve", "Someone", "changed my mind", false));
        assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
    }

    @Test
    void test_a_reviewer_block_needs_an_explicit_override() {
        String rid = newRun(runner(GOOD, BLOCK));
        assertEquals("needs_rework", store.run(rid).status());
        var e = assertThrows(Pipeline.GateError.class, () -> pipeline.decide(rid, "approve", "Jai", "looks fine", false));
        assertEquals("the reviewer blocked this proposal; approving it needs --override and a reason", e.getMessage());
        pipeline.decide(rid, "approve", "Jai", "memory fix shipped in 2.4.1", true);
        assertEquals(1, store.approval(rid).override());
    }

    @Test
    void test_a_reviewer_revise_is_not_an_approval() {
        // REVISE = "right facts, too big a change". Approving the ORIGINAL change must not be one click.
        String rid = newRun(runner(GOOD, REVISE));
        assertEquals("needs_rework", store.run(rid).status());
        var e = assertThrows(Pipeline.GateError.class, () -> pipeline.decide(rid, "approve", "Jai", "looks fine", false));
        assertTrue(e.getMessage().contains("safer change"), e.getMessage());
        pipeline.decide(rid, "approve", "Jai", "accepting the full change; memory fix confirmed", true);
        assertEquals(1, store.approval(rid).override());
    }

    @Test
    void test_a_second_decision_is_a_conflict_even_when_it_races() {
        String rid = newRun(runner(GOOD, APPROVE));
        store.recordDecision(rid, "approve", "Jai", "first", false);
        // what a second process racing past decide()'s check hits
        assertThrows(Store.Conflict.class, () -> store.recordDecision(rid, "reject", "Asha", "second", false));
    }

    // --- contracts between stages
    @Test
    void test_a_proposal_outside_the_contract_never_reaches_the_gate() {
        String bad = goodWith("{\"action\": \"delete_account\", \"key\": \"ingest.max_concurrent_jobs\"}");
        assertThrows(RuntimeException.class, () -> newRun(runner(bad, null)));
        Store.RunRow r = store.runs().get(0);
        assertEquals("investigate_failed", r.status());
        assertNull(store.stage(r.id(), "review"));
    }

    @Test
    void test_a_key_outside_the_allowlist_is_refused_even_when_complete() {
        String bad = goodWith("{\"action\": \"update_config\", \"key\": \"feature.ai_triage_enabled\", \"value\": false, \"expected_version\": 1}");
        assertThrows(RuntimeException.class, () -> newRun(runner(bad, null)));
        assertEquals("investigate_failed", store.runs().get(0).status());
    }

    @Test
    void test_a_malformed_verdict_is_a_failed_review() {
        String bad = APPROVE.replace("\"verdict\": \"approve\"", "\"verdict\": \"ship it\"");
        assertThrows(Contracts.ContractError.class, () -> newRun(runner(GOOD, bad)));
        assertEquals("review_failed", store.runs().get(0).status());
    }

    @Test
    void test_an_incomplete_change_is_a_failed_stage_not_a_guess() {
        String bad = goodWith("{\"action\": \"update_config\", \"key\": \"ingest.max_concurrent_jobs\", \"value\": 8}");
        var e = assertThrows(Contracts.ContractError.class, () -> newRun(runner(bad, null)));
        assertTrue(e.getMessage().contains("update_config needs [expected_version]"), e.getMessage());
    }

    @Test
    void test_nothing_to_change_ends_the_run_without_a_review() {
        FakeRunner r = runner(goodWith("{\"action\": \"none\"}"), null);
        String rid = newRun(r);
        assertEquals("no_change", store.run(rid).status());
        assertEquals(List.of("investigate"), r.calls);
    }

    // --- checkpointing
    @Test
    void test_resume_skips_finished_stages() {
        FakeRunner r = new FakeRunner().on("investigate", GOOD)
                .on("review", new RunnerException("review: structured output failed", 0.11, 3), APPROVE);
        assertThrows(RunnerException.class, () -> newRun(r));
        String rid = store.runs().get(0).id();
        assertEquals("review_failed", store.run(rid).status());
        pipeline.resume(r, rid);
        assertEquals(List.of("investigate", "review", "review"), r.calls, "investigate was paid for twice");
        assertEquals("awaiting_approval", store.run(rid).status());
        assertEquals(0.03 + 0.11 + 0.03, store.cost(rid), 1e-4, "the failed attempt was paid for - it must show in the cost");
        assertEquals(2, store.stage(rid, "review").attempt());
    }

    @Test
    void a_failed_stage_records_what_it_cost_and_why() {
        FakeRunner r = new FakeRunner().on("investigate", new RunnerException("investigate: error_max_turns after 14 turns", 0.25, 14));
        assertThrows(RunnerException.class, () -> newRun(r));
        String rid = store.runs().get(0).id();
        Store.StageRow s = store.stage(rid, "investigate");
        assertEquals("failed", s.status());
        assertEquals(0.25, s.costUsd(), 1e-9);
        assertEquals("RunnerError: investigate: error_max_turns after 14 turns", s.error());
        assertEquals("investigate_failed", store.run(rid).status());
    }

    // --- the write: orchestrator-owned retry state
    @Test
    void test_unknown_outcome_is_retried_with_the_same_operation_id() throws Exception {
        String change = goodWith("{\"action\": \"add_ticket_comment\", \"ticket_id\": \"T-1001\", \"comment\": \"Concurrency raised to 8 after approval.\"}");
        String rid = newRun(runner(change, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "comment only", false);
        ops.latencyMs = 800;
        pipeline.applyTimeout = Duration.ofMillis(300);
        assertEquals("outcome_unknown", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        String op1 = store.operation(rid).opId();
        Thread.sleep(1000);                                                  // the write landed anyway
        ops.latencyMs = 0;
        pipeline.applyTimeout = Duration.ofSeconds(5);
        assertEquals("applied", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        assertEquals(op1, store.operation(rid).opId());
        assertEquals(List.of(op1, op1), ops.idempotencyKeys, "both attempts must carry the same Idempotency-Key");
        assertEquals(List.of("Concurrency raised to 8 after approval."), ops.comments.get("T-1001"));
        assertTrue(store.operation(rid).response().contains("\"_replayed\": true"), "the retry was answered from the idempotency store");
    }

    @Test
    void a_5xx_is_an_unknown_outcome_and_apply_again_is_safe() {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "halfway step", false);
        ops.forceStatus = 503;
        assertEquals("outcome_unknown", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        assertEquals("pending", store.operation(rid).status());
        ops.forceStatus = 0;
        assertEquals("applied", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        assertEquals(1, ops.writesApplied.get());
        assertEquals(8, ops.config.get("ingest.max_concurrent_jobs").value().asInt());
    }

    @Test
    void a_4xx_is_a_failed_apply_not_a_retry() {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "halfway step", false);
        ops.forceStatus = 409;
        assertEquals("apply_failed", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url).status());
        assertEquals("failed", store.operation(rid).status());
        assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
    }

    @Test
    void aira_ops_down_is_an_unknown_outcome() {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "halfway step", false);
        String deadUrl = ops.url;
        ops.close();
        assertEquals("outcome_unknown", pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, deadUrl).status());
        assertTrue(store.operation(rid).response().contains("unavailable"));
    }

    // --- least privilege
    @Test
    void test_the_agents_token_cannot_write() {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "test", false);
        assertEquals("apply_failed", pipeline.apply(rid, AiraOpsStub.READ_TOKEN, ops.url).status());
        assertEquals("forbidden", Store.parse(store.operation(rid).response()).path("error").path("code").asText());
        assertEquals(0, ops.writesApplied.get());
    }

    @Test
    void test_an_agent_will_not_start_while_the_write_token_is_in_the_environment() {
        ModelClient never = (m, t, s, n) -> { throw new AssertionError("the model must not be called"); };
        for (String held : GatewayAgentRunner.FORBIDDEN_ENV) {
            var runner = new GatewayAgentRunner(never, "claude-sonnet", ops.url, AiraOpsStub.READ_TOKEN, 14, 0.4,
                    k -> k.equals(held) ? "secret-for-test" : null);
            var e = assertThrows(RunnerException.class, () -> runner.run("investigate", "sys", "p", Contracts.PROPOSAL));
            assertTrue(e.getMessage().startsWith("refusing to start the investigate agent: " + held), e.getMessage());
        }
    }

    @Test
    void test_stage_tools_are_read_only() {
        // The Python test checks the SDK options (no built-in tools, allow-listed MCP tools). Here the
        // agent's whole world is the tool list sent to the model: four read tools and submit_result.
        List<List<String>> seen = new ArrayList<>();
        ModelClient model = (messages, tools, system, maxTokens) -> {
            List<String> names = new ArrayList<>();
            for (Object t : tools) names.add(((JsonNode) t).path("name").asText());
            seen.add(names);
            ObjectNode r = Contracts.object();
            r.putObject("usage").put("input_tokens", 10).put("output_tokens", 10);
            ArrayNode content = r.putArray("content");
            content.addObject().put("type", "tool_use").put("id", "tu_1").put("name", "submit_result")
                    .set("input", json(goodWith("{\"action\": \"none\"}")));
            return r;
        };
        var runner = new GatewayAgentRunner(model, "claude-sonnet", ops.url, AiraOpsStub.READ_TOKEN, 14, 0.4, k -> null);
        runner.run("investigate", Agents.INVESTIGATE_SYSTEM, "p", Contracts.PROPOSAL);
        List<String> expected = new ArrayList<>(AiraOpsTools.READ_TOOLS);
        expected.add("submit_result");
        assertEquals(List.of(expected), seen);
    }

    // --- replay, binding, resume
    @Test
    void test_the_saved_blocked_run_replays_and_the_gate_holds() {
        String rid = pipeline.replay(Cli.resolveFixture("fixtures/blocked-36cc478fce.json"));
        assertEquals("needs_rework", store.run(rid).status());                         // the reviewer's BLOCK
        var e = assertThrows(Pipeline.GateError.class, () -> pipeline.decide(rid, "approve", "Jai", "backlog is P1", false));
        assertTrue(e.getMessage().contains("override"));                                  // no --override: refused
        pipeline.decide(rid, "reject", "Jai", "confirm the memory fix first", false);
        var e2 = assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
        assertTrue(e2.getMessage().contains("no approval on record"));
        assertEquals(0.0, store.cost(rid));
        assertTrue(store.run(rid).question().endsWith("  [replay of 36cc478fce]"));
    }

    @Test
    void test_an_approval_is_bound_to_the_exact_proposal() throws Exception {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "test", false);
        ObjectNode changed = (ObjectNode) json(GOOD);
        ((ObjectNode) changed.get("proposed_change")).put("value", 16);
        try (Connection c = DriverManager.getConnection("jdbc:sqlite:" + store.path);
             PreparedStatement ps = c.prepareStatement("update stages set output=? where run_id=? and name='investigate'")) {
            ps.setString(1, PyJson.dumps(changed));
            ps.setString(2, rid);
            ps.executeUpdate();
        }
        var e = assertThrows(Pipeline.GateError.class, () -> pipeline.apply(rid, AiraOpsStub.WRITE_TOKEN, ops.url));
        assertTrue(e.getMessage().contains("changed after it was decided"), e.getMessage());
        assertEquals(0, ops.writesApplied.get());
    }

    @Test
    void test_resume_never_writes() {
        String rid = newRun(runner(GOOD, APPROVE));
        pipeline.decide(rid, "approve", "Jai", "test", false);
        pipeline.resume(new FakeRunner(), rid);
        assertEquals("approved", store.run(rid).status());
        assertNull(store.operation(rid));
    }

    // --- the Java port stays in sync with the Python lab
    @Test
    void the_proposal_sha_is_the_same_bytes_as_python() throws Exception {
        JsonNode fx = Contracts.JSON.readTree(Cli.resolveFixture("fixtures/blocked-36cc478fce.json").toFile());
        String rid = pipeline.replay(Cli.resolveFixture("fixtures/blocked-36cc478fce.json"));
        assertEquals("d1f8289ff76394e7048ba6d016aaf20da82ac9caa8b5155ebea9ad0034f68315", store.proposalSha(rid));
        assertTrue(fx.has("stages"));
    }

    @Test
    void pyjson_writes_what_python_json_dumps_writes() {
        JsonNode n = json("{\"a\": [1, 2.5, {\"b\": \"\u2192 \\\"x\\\"\\n\"}], \"c\": {}, \"d\": [], \"e\": null, \"f\": true, "
                + "\"g\": 0.0001, \"h\": 1e-05, \"i\": 16.0}");
        assertEquals("{\n  \"a\": [\n    1,\n    2.5,\n    {\n      \"b\": \"\\u2192 \\\"x\\\"\\n\"\n    }\n  ],\n  \"c\": {},\n"
                + "  \"d\": [],\n  \"e\": null,\n  \"f\": true,\n  \"g\": 0.0001,\n  \"h\": 1e-05,\n  \"i\": 16.0\n}", PyJson.dumps(n, 2));
        assertEquals("{\"a\": [1, 2.5, {\"b\": \"x\"}], \"c\": {}}", PyJson.dumps(json("{\"a\": [1, 2.5, {\"b\": \"x\"}], \"c\": {}}")));
    }

    @Test
    void the_prompts_match_agents_py() throws Exception {
        String py = Files.readString(Spans.repoRoot().resolve("day4-orchestration-evals-cicd/lab5-1-handoff/agents.py"));
        assertEquals(pythonString(py, "INVESTIGATE_SYSTEM"), Agents.INVESTIGATE_SYSTEM, "keep in sync with lab5-1-handoff/agents.py");
        assertEquals(pythonString(py, "REVIEW_SYSTEM"), Agents.REVIEW_SYSTEM, "keep in sync with lab5-1-handoff/agents.py");
    }

    /** NAME = ( "..." "..." ) - adjacent double-quoted literals, joined. */
    static String pythonString(String src, String name) {
        String block = src.split(name + " = \\(", 2)[1].split("\"\\)\n", 2)[0] + "\"";
        Matcher m = Pattern.compile("\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(block);
        StringBuilder sb = new StringBuilder();
        while (m.find()) sb.append(m.group(1).replace("\\\"", "\"").replace("\\n", "\n"));
        return sb.toString();
    }

    @Test
    void the_four_exercise_regions_are_marked() throws Exception {
        String src = Files.readString(Path.of("src/main/java/com/airamatrix/day4/lab51/Pipeline.java"));
        for (int n = 1; n <= 4; n++) {
            assertEquals(1, count(src, "// >>> TODO " + n + ":"), "TODO " + n + " start marker");
            assertEquals(1, count(src, "// <<< TODO " + n + "\n"), "TODO " + n + " end marker");
            assertTrue(src.indexOf("// >>> TODO " + n + ":") < src.indexOf("// <<< TODO " + n + "\n"));
        }
    }

    static int count(String s, String needle) {
        int c = 0;
        for (int i = s.indexOf(needle); i >= 0; i = s.indexOf(needle, i + 1)) c++;
        return c;
    }
}
