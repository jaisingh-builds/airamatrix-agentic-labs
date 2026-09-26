package com.airamatrix.day4.lab52;

import java.io.PrintStream;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.GatewayAgentRunner;
import com.airamatrix.labkit.Config;
import com.airamatrix.labkit.GatewayClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * Lab 5.2 - run the golden set against the investigation agent and gate on it. Port of run_evals.py.
 *
 * <pre>
 *   java -jar lab52.jar                          # every case once
 *   java -jar lab52.jar --repeat 3 --cases backlog-cause,injection-t1007   # consistency
 *   java -jar lab52.jar --regrade fixtures/live-runs.json   # re-grade saved runs, no model, free
 *   java -jar lab52.jar                          # also the CI gate: the threshold is frozen in the
 *                                                # golden file; CI refuses --min-pass (local experiments only)
 * </pre>
 *
 * Exit codes: 0 gate passed · 1 gate failed · 2 could not run (setup error, budget).
 * One private aira-ops per invocation with fresh data and a read-only caller token, so cases
 * can't leak state from an earlier session and no case can change anything.
 */
public final class RunEvals {
    private RunEvals() {}

    /** What a live run needs from the outside world. Tests replace it; nothing here is called offline. */
    public interface Setup {
        /** The runner factory. Throws IllegalStateException when the gateway config is missing. */
        EvalHarness.RunnerFactory runners();
        EvalHarness.OpsHandle startOps() throws Exception;
        /** Where eval-*.json goes (kept for --regrade). */
        default Path resultsDir() { return LabPaths.module().resolve("results"); }
    }

    /** The real thing: the gateway from .env, GatewayAgentRunner, and a python3 aira-ops. */
    public static Setup liveSetup(Map<String, String> env) {
        return new Setup() {
            @Override public EvalHarness.RunnerFactory runners() {
                Config cfg = new Config().require();          // no gateway config -> exit 2
                GatewayClient gw = new GatewayClient(cfg);
                // scrub_agent_environment(): the agent's process view holds nothing secret but the gateway key.
                var agentEnv = Agents.scrubbed(env);
                return (url, token, budget) -> new GatewayAgentRunner(gw::messages, cfg.model, url, token, 14, budget, agentEnv);
            }
            @Override public EvalHarness.OpsHandle startOps() throws Exception {
                Map<String, String> child = new HashMap<>(env);
                child.keySet().removeAll(Agents.secretNames(env));
                String python = env.getOrDefault("LAB_PYTHON",
                        System.getProperty("os.name", "").toLowerCase().contains("win") ? "python" : "python3");
                return EvalHarness.Ops.start(python, LabPaths.ops(), child);
            }
        };
    }

    static final List<String> OPTIONS = List.of("golden", "cases", "repeat", "workers", "min-pass", "retry-errors",
            "budget", "per-run-budget", "regrade");

    static final String USAGE = """
            usage: lab52 [-h] [--golden GOLDEN] [--cases CASES] [--repeat REPEAT] [--workers WORKERS]
                         [--min-pass MIN_PASS] [--retry-errors RETRY_ERRORS] [--budget BUDGET]
                         [--per-run-budget PER_RUN_BUDGET] [--regrade RESULTS_JSON]
                   lab52 judge calibrate [--mode {blind,reference,both}] [--set SET]""";

    static final String HELP = USAGE + """


            options:
              --golden GOLDEN       golden set (default: lab5-2-evals/golden/cases.json)
              --cases CASES         comma-separated case ids
              --repeat REPEAT       runs per case (default 1)
              --workers WORKERS     parallel runs (default 3)
              --min-pass MIN_PASS   override the threshold frozen in the golden file (local experiments only)
              --retry-errors N      re-run a run that ERRORED (not one that failed) this many times (default 1)
              --budget BUDGET       stop starting runs above this spend (default $EVAL_BUDGET_USD or 2.0)
              --per-run-budget X    budget for one agent run (default 0.40)
              --regrade RESULTS_JSON  re-grade saved runs: no model, $0""";

    public static int main(String[] argv, PrintStream out, PrintStream err, Map<String, String> env, Setup setup) throws Exception {
        String golden;
        String casesArg;
        int repeat, workers, retryErrors;
        Double minPass;
        double budget, perRunBudget;
        String regrade;
        try {
            Args a = Args.parse(argv, 0, OPTIONS);
            if (a.help) { out.println(HELP); return 0; }
            if (!a.positional().isEmpty()) throw new Args.UsageError("unrecognized arguments: " + String.join(" ", a.positional()));
            golden = a.str("golden", null);
            casesArg = a.str("cases", null);
            repeat = a.integer("repeat", 1);
            workers = a.integer("workers", 3);
            if (workers < 1) throw new Args.UsageError("argument --workers: must be at least 1");
            minPass = a.dbl("min-pass", null);
            retryErrors = a.integer("retry-errors", 1);
            String envBudget = env.get("EVAL_BUDGET_USD");
            double dfltBudget;
            try { dfltBudget = envBudget == null || envBudget.isEmpty() ? 2.0 : Double.parseDouble(envBudget); }
            catch (NumberFormatException e) { throw new Args.UsageError("EVAL_BUDGET_USD is not a number: '" + envBudget + "'"); }
            budget = a.dbl("budget", dfltBudget);
            perRunBudget = a.dbl("per-run-budget", 0.40);
            regrade = a.str("regrade", null);
        } catch (Args.UsageError e) {
            err.println(USAGE);
            err.println("lab52: error: " + e.getMessage());
            return 2;
        }

        Path goldenPath = golden == null ? LabPaths.lab().resolve("golden/cases.json") : LabPaths.resolve(golden);
        JsonNode goldenDoc = LabPaths.readJson(goldenPath);
        double frozen = goldenDoc.get("gate").get("min_pass_rate").asDouble();
        if (minPass != null && minPass != frozen) {
            String ci = env.get("CI");
            if (ci != null && !ci.isEmpty()) {
                err.println("setup: --min-pass " + Py.floatRepr(minPass) + " would override the frozen " + Py.floatRepr(frozen)
                        + "; change golden file instead");
                return 2;
            }
            err.println("warning: overriding the frozen threshold " + Py.floatRepr(frozen) + " with " + Py.floatRepr(minPass) + " (local only)");
        }
        double min = minPass != null ? minPass : frozen;
        List<String> wanted = casesArg == null || casesArg.isEmpty() ? null : Arrays.asList(casesArg.split(","));
        List<JsonNode> cases = new ArrayList<>();
        for (JsonNode c : goldenDoc.get("cases")) if (wanted == null || wanted.contains(c.get("id").asText())) cases.add(c);
        Map<String, Boolean> hasCritical = new LinkedHashMap<>();
        for (JsonNode c : cases) {
            boolean crit = false;
            for (JsonNode ch : c.get("checks")) crit |= ch.path("critical").asBoolean(false);
            hasCritical.put(c.get("id").asText(), crit);
        }
        if (cases.isEmpty()) {
            err.println("no matching cases");
            return 2;
        }
        String suite = goldenDoc.path("suite").asText();

        if (regrade != null) {                          // grader development: no model, no cost
            JsonNode prev = LabPaths.readJson(LabPaths.resolve(regrade));
            Map<String, JsonNode> byId = new LinkedHashMap<>();
            for (JsonNode c : cases) byId.put(c.get("id").asText(), c);
            List<ObjectNode> results = new ArrayList<>();
            for (JsonNode c : prev.get("cases")) {
                String id = c.get("id").asText();
                if (!byId.containsKey(id)) continue;
                ObjectNode cr = Contracts.object();
                cr.put("id", id);
                cr.put("has_critical", hasCritical.get(id));
                ArrayNode runs = cr.putArray("runs");
                for (JsonNode r : c.get("runs")) {
                    if (r.has("raw")) {
                        ObjectNode copy = r.deepCopy();
                        copy.set("grade", Graders.gradeCase(byId.get(id), r.get("raw")));
                        runs.add(copy);
                    } else {
                        runs.add(r);
                    }
                }
                results.add(cr);
            }
            ObjectNode g = Graders.gate(results, min);
            EvalHarness.report(suite + " (re-graded)", results, g, 0.0, out, env.get("GITHUB_STEP_SUMMARY"));
            return g.get("ok").asBoolean() ? 0 : 1;
        }

        EvalHarness.RunnerFactory runners;
        try {
            runners = setup.runners();
        } catch (IllegalStateException e) {
            err.println("setup: " + e.getMessage());
            return 2;
        }
        Map<String, ObjectNode> results = new LinkedHashMap<>();
        for (JsonNode c : cases) {
            ObjectNode cr = Contracts.object();
            cr.put("id", c.get("id").asText());
            cr.put("has_critical", hasCritical.get(c.get("id").asText()));
            cr.putArray("runs");
            results.put(c.get("id").asText(), cr);
        }
        double spent = 0.0;
        boolean overBudget = false;
        EvalHarness.OpsHandle ops;
        try {
            ops = setup.startOps();
        } catch (Exception e) {
            err.println("setup: could not start aira-ops: " + e.getMessage());
            return 2;
        }
        ExecutorService pool = Executors.newFixedThreadPool(workers);
        try (ops) {
            CompletionService<ObjectNode> cs = new ExecutorCompletionService<>(pool);
            Map<Future<ObjectNode>, JsonNode> jobs = new LinkedHashMap<>();
            for (int rep = 0; rep < repeat; rep++) {
                for (JsonNode c : cases) {
                    jobs.put(cs.submit(() -> EvalHarness.runOne(c, ops, perRunBudget, runners)), c);
                }
            }
            for (int left = jobs.size(); left > 0; left--) {
                Future<ObjectNode> fut = cs.take();
                if (fut.isCancelled()) continue;            // cancelled after the budget ran out
                JsonNode kase = jobs.get(fut);
                String id = kase.get("id").asText();
                ObjectNode r;
                try {
                    r = fut.get();
                } catch (ExecutionException e) {           // runOne never throws; belt and braces
                    r = Contracts.object().put("error", String.valueOf(e.getCause())).put("cost_usd", 0.0);
                }
                for (int i = 0; i < retryErrors; i++) {     // an ERROR (no valid output) is retried; a FAIL never is
                    if (!r.has("error")) break;
                    spent += r.get("cost_usd").asDouble();
                    out.println("  RETRY " + Py.left(id, 22) + " after: " + Py.head(r.get("error").asText(), 70));
                    out.flush();
                    ObjectNode again = EvalHarness.runOne(kase, ops, perRunBudget, runners);
                    again.put("retried_after", Py.head(r.get("error").asText(), 200));
                    again.put("errored_cost_usd", r.get("cost_usd").asDouble());
                    r = again;
                }
                spent += r.get("cost_usd").asDouble();
                ((ArrayNode) results.get(id).get("runs")).add(r);
                String status = r.path("grade").path("passed").asBoolean(false) ? "PASS" : (r.has("error") ? "ERROR" : "FAIL");
                out.println("  " + Py.left(status, 5) + " " + Py.left(id, 22) + " $" + Py.fixed(r.get("cost_usd").asDouble(), 3)
                        + " " + Py.seconds(r.get("seconds").asDouble()) + "s");
                out.flush();
                if (spent > budget) {
                    overBudget = true;
                    err.println("budget $" + Py.floatRepr(budget) + " exceeded ($" + Py.fixed(spent, 2) + ") - cancelling the rest");
                    for (Future<ObjectNode> f : jobs.keySet()) f.cancel(false);
                }
            }
        } finally {
            pool.shutdownNow();
        }
        List<ObjectNode> caseResults = new ArrayList<>();
        for (JsonNode c : cases) caseResults.add(results.get(c.get("id").asText()));
        ObjectNode g = Graders.gate(caseResults, min);
        Path file = setup.resultsDir().resolve("eval-" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")) + ".json");
        ObjectNode doc = Contracts.object();
        doc.put("suite", suite);
        doc.set("gate", g);
        doc.put("cost_usd", Py.round(spent, 4));
        doc.set("cases", Contracts.JSON.valueToTree(caseResults));
        EvalHarness.writeResults(file, doc);
        EvalHarness.report(suite, caseResults, g, spent, out, env.get("GITHUB_STEP_SUMMARY"));
        out.println("\nresults: " + LabPaths.show(file));
        if (overBudget) return 2;                       // could not run the whole suite: no verdict
        if (g.get("errors").asInt() == g.get("runs").asInt()) return 2;
        return g.get("ok").asBoolean() ? 0 : 1;
    }
}
