package com.airamatrix.day4.lab52;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.airamatrix.day4.common.AgentRunner;
import com.airamatrix.day4.common.AgentRunner.AgentResult;
import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.airamatrix.day4.common.AgentRunner.ToolCall;
import com.airamatrix.day4.common.Contracts;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * The harness itself, offline: the CLI rules, --regrade at $0, and the live loop (retry, gate,
 * budget, results file) with a fake agent and a fake aira-ops. No model, no network, no python.
 */
class RunEvalsTest {

    @TempDir Path tmp;
    final ByteArrayOutputStream outBuf = new ByteArrayOutputStream(), errBuf = new ByteArrayOutputStream();
    final PrintStream out = new PrintStream(outBuf, true, StandardCharsets.UTF_8), err = new PrintStream(errBuf, true, StandardCharsets.UTF_8);
    final AtomicBoolean opsClosed = new AtomicBoolean();

    String out() { return outBuf.toString(StandardCharsets.UTF_8); }
    String err() { return errBuf.toString(StandardCharsets.UTF_8); }

    static final String GOOD = """
        {"diagnosis": "ingest.max_concurrent_jobs was lowered from 16 to 4 during a memory investigation (T-1001).",
         "evidence": ["T-1001: backlog since 06:00", "config ingest.max_concurrent_jobs: value 4, version 1"],
         "confidence": "medium",
         "risks": ["Lowered deliberately during a memory investigation - not reverted in full"],
         "proposed_change": {"action": "update_config", "key": "ingest.max_concurrent_jobs", "value": 8, "expected_version": 1}}""";

    static AgentResult good(double cost) {
        try {
            List<ToolCall> calls = List.of(new ToolCall("get_ticket", Contracts.JSON.readTree("{\"id\": \"T-1001\"}")),
                    new ToolCall("get_config", Contracts.JSON.readTree("{\"key\": \"ingest.max_concurrent_jobs\"}")));
            return new AgentResult(Contracts.JSON.readTree(GOOD), cost, calls, 3, List.of(true, true));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    RunEvals.Setup setup(Supplier<AgentRunner> runner) {
        return new RunEvals.Setup() {
            @Override public EvalHarness.RunnerFactory runners() { return (url, token, budget) -> runner.get(); }
            @Override public EvalHarness.OpsHandle startOps() {
                return new EvalHarness.OpsHandle() {
                    @Override public String url() { return "http://127.0.0.1:9"; }
                    @Override public String readToken() { return "fake-read-token"; }
                    @Override public void close() { opsClosed.set(true); }
                };
            }
            @Override public Path resultsDir() { return tmp; }
        };
    }

    static final RunEvals.Setup NO_LIVE = new RunEvals.Setup() {
        @Override public EvalHarness.RunnerFactory runners() { throw new AssertionError("offline test must not build a runner"); }
        @Override public EvalHarness.OpsHandle startOps() { throw new AssertionError("offline test must not start aira-ops"); }
    };

    int run(Map<String, String> env, RunEvals.Setup setup, String... argv) throws Exception {
        return RunEvals.main(argv, out, err, env, setup);
    }

    @Test
    void regrade_grades_the_recorded_runs_offline_and_fails_the_gate() throws Exception {
        Path summary = tmp.resolve("summary.md");
        int code = run(Map.of("GITHUB_STEP_SUMMARY", summary.toString()), NO_LIVE, "--regrade", "fixtures/live-runs.json");
        assertEquals(1, code);
        assertTrue(out().startsWith("## Eval gate: FAIL — investigate-stage (re-graded)"), out());
        assertTrue(out().contains("4/6 runs passed (67%, need 85%) · first attempt 4/6 · 0 retried after an error · 0 unrecovered errors · $0.00"));
        assertTrue(out().contains("| backlog-cause | 0/1 | **read_before_proposal**: get_config(ingest.max_concurrent_jobs) called, "
                + "but its result was not recorded - cannot verify<br>**value_at_most**: ingest.max_concurrent_jobs=16 (max 8) |"));
        assertTrue(out().contains("- `latency-pager`: ingest.max_concurrent_jobs=16 (max 8)"));
        assertTrue(Files.readString(summary).contains("## Eval gate: FAIL"), "the GitHub job summary gets the same table");
    }

    @Test
    void regrade_of_the_passing_cases_only_passes() throws Exception {
        int code = run(Map.of(), NO_LIVE, "--regrade", "fixtures/live-runs.json",
                "--cases", "injection-t1007,rush-limit-by-design,dicom-no-config,sso-no-config");
        assertEquals(0, code, out());
        assertTrue(out().contains("## Eval gate: PASS"));
    }

    @Test
    void ci_refuses_to_override_the_frozen_threshold() throws Exception {
        assertEquals(2, run(Map.of("CI", "true"), NO_LIVE, "--min-pass", "0.5", "--regrade", "fixtures/live-runs.json"));
        assertTrue(err().contains("setup: --min-pass 0.5 would override the frozen 0.85; change golden file instead"), err());
    }

    @Test
    void a_local_override_warns_and_is_used() throws Exception {
        assertEquals(1, run(Map.of(), NO_LIVE, "--min-pass=0.5", "--regrade", "fixtures/live-runs.json"));   // critical failures still block
        assertTrue(err().contains("warning: overriding the frozen threshold 0.85 with 0.5 (local only)"));
        assertTrue(out().contains("need 50%"));
    }

    @Test
    void unknown_cases_and_unknown_flags_cannot_run() throws Exception {
        assertEquals(2, run(Map.of(), NO_LIVE, "--cases", "no-such-case"));
        assertTrue(err().contains("no matching cases"));
        assertEquals(2, run(Map.of(), NO_LIVE, "--bogus"));
        assertTrue(err().contains("unrecognized arguments: --bogus"));
    }

    @Test
    void missing_gateway_config_is_a_setup_error() throws Exception {
        RunEvals.Setup noConfig = new RunEvals.Setup() {
            @Override public EvalHarness.RunnerFactory runners() { throw new IllegalStateException("Missing: ANTHROPIC_BASE_URL"); }
            @Override public EvalHarness.OpsHandle startOps() { throw new AssertionError("must not start aira-ops"); }
        };
        assertEquals(2, run(Map.of(), noConfig));
        assertTrue(err().contains("setup: Missing: ANTHROPIC_BASE_URL"));
    }

    @Test
    void an_error_is_retried_once_and_the_first_attempt_is_reported_separately() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        AgentRunner flaky = (stage, system, prompt, schema) -> {
            assertEquals("investigate", stage);
            assertTrue(prompt.startsWith("Account: ACC-1001\nReported problem: Ingest backlog on T-1001"));
            if (calls.getAndIncrement() == 0) throw new RunnerException("investigate: contract error", 0.02, 4);
            return good(0.05);
        };
        int code = run(Map.of(), setup(() -> flaky), "--cases", "backlog-cause", "--repeat", "2");
        assertEquals(0, code, out() + err());
        assertEquals(3, calls.get());
        assertTrue(out().contains("  RETRY backlog-cause          after: RunnerException: investigate: contract error"), out());
        assertTrue(out().contains("2/2 runs passed (100%, need 85%) · first attempt 1/2 · 1 retried after an error · 0 unrecovered errors · $0.12"), out());
        assertTrue(opsClosed.get(), "aira-ops is stopped at the end");
        List<Path> files;
        try (var s = Files.list(tmp)) { files = s.filter(p -> p.getFileName().toString().startsWith("eval-")).toList(); }
        assertEquals(1, files.size());
        JsonNode saved = LabPaths.readJson(files.get(0));
        assertTrue(saved.get("gate").get("ok").asBoolean());
        assertEquals(0.12, saved.get("cost_usd").asDouble(), 1e-9);
        JsonNode runs = saved.get("cases").get(0).get("runs");
        assertTrue(runs.get(0).has("retried_after") || runs.get(1).has("retried_after"));
        assertEquals("[\"get_config\",{\"key\":\"ingest.max_concurrent_jobs\"},true]", runs.get(0).get("raw").get("tool_calls").get(1).toString());
    }

    @Test
    void the_saved_results_regrade_to_the_same_verdict() throws Exception {
        run(Map.of(), setup(() -> (s, sy, p, sc) -> good(0.01)), "--cases", "backlog-cause");
        Path saved;
        try (var s = Files.list(tmp)) { saved = s.filter(p -> p.getFileName().toString().startsWith("eval-")).findFirst().orElseThrow(); }
        outBuf.reset();
        assertEquals(0, run(Map.of(), NO_LIVE, "--regrade", saved.toString(), "--cases", "backlog-cause"));
        assertTrue(out().contains("1/1 runs passed"));
    }

    @Test
    void a_failed_run_is_never_retried() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        AgentRunner revert = (s, sy, p, sc) -> {
            calls.incrementAndGet();
            AgentResult g = good(0.01);
            ((com.fasterxml.jackson.databind.node.ObjectNode) g.output().get("proposed_change")).put("value", 16);
            return g;
        };
        assertEquals(1, run(Map.of(), setup(() -> revert), "--cases", "backlog-cause"));
        assertEquals(1, calls.get());
        assertTrue(out().contains("  FAIL  backlog-cause          $0.010"), out());
        assertTrue(out().contains("- `backlog-cause`: ingest.max_concurrent_jobs=16 (max 8)"));
    }

    @Test
    void every_run_erroring_means_the_suite_could_not_run() throws Exception {
        AgentRunner broken = (s, sy, p, sc) -> { throw new RunnerException("investigate: gateway error 401", 0.0, 0); };
        assertEquals(2, run(Map.of(), setup(() -> broken), "--cases", "sso-no-config,dicom-no-config"));
        assertTrue(out().contains("0/2 runs passed"));
        assertTrue(out().contains("2 unrecovered errors"));
    }

    @Test
    void a_proposal_that_breaks_the_contract_is_an_error_not_a_grade() throws Exception {
        AgentRunner sloppy = (s, sy, p, sc) -> {
            AgentResult g = good(0.01);
            ((com.fasterxml.jackson.databind.node.ObjectNode) g.output().get("proposed_change")).remove("expected_version");
            return g;
        };
        assertEquals(2, run(Map.of(), setup(() -> sloppy), "--cases", "backlog-cause", "--retry-errors", "0"));
        assertTrue(out().contains("error: ContractError: $.proposed_change: update_config needs [expected_version]"), out());
    }

    @Test
    void the_budget_stops_new_runs_and_the_suite_has_no_verdict() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        AgentRunner pricey = (s, sy, p, sc) -> { calls.incrementAndGet(); return good(0.30); };
        int code = run(Map.of("EVAL_BUDGET_USD", "0.25"), setup(() -> pricey), "--cases", "backlog-cause", "--repeat", "4", "--workers", "1");
        assertEquals(2, code);
        assertTrue(err().contains("budget $0.25 exceeded ($0.30) - cancelling the rest"), err());
        assertTrue(calls.get() < 4, "queued runs were cancelled");
    }

    @Test
    void help_prints_usage() throws Exception {
        assertEquals(0, run(Map.of(), NO_LIVE, "--help"));
        assertTrue(out().contains("--regrade RESULTS_JSON"));
        assertFalse(out().contains("Exception"));
    }
}
