package com.airamatrix.capstone;

import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.function.Function;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.ModelClient;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * The eval harness: every golden case x repeats, graded by {@link Checks}, gated, written to a results file
 * and a Markdown report. A RUN is one attempt plus at most one retry after an ERROR (no proposal) - never
 * after a FAIL. The first-attempt pass count is reported beside the final one, so a retry cannot hide flakiness.
 *
 * Targets: local (a private aira-ops per eval with fresh seed data + the training gateway) or, from
 * aws-tools, the deployed AgentCore runtime. Grading is identical.
 *
 * Exit codes: 0 gate passed, 1 gate failed, 2 could not run.
 */
public final class Evals {
    private Evals() {}

    /** One attempt at one case. Returns {status, proposal, verdict, trajectory, cost_usd, trace} or {error, cost_usd}. Never throws. */
    @FunctionalInterface
    public interface CaseRunner { ObjectNode run(JsonNode kase); }

    public static JsonNode load(Path golden) throws Exception {
        JsonNode g = Contracts.JSON.readTree(golden.toFile());
        for (JsonNode c : g.path("cases")) {
            for (JsonNode chk : c.path("checks")) {
                if (!Checks.KNOWN.contains(chk.path("check").asText())) {
                    throw new IllegalArgumentException(c.path("id").asText() + ": unknown check " + chk.path("check").asText());
                }
            }
        }
        return g;
    }

    public static List<JsonNode> select(JsonNode golden, String ids) {
        List<JsonNode> out = new ArrayList<>();
        for (JsonNode c : golden.path("cases")) {
            if (ids == null || ids.isBlank() || List.of(ids.split(",")).contains(c.path("id").asText())) out.add(c);
        }
        return out;
    }

    /** Runs, grades, gates, writes results + report. */
    public static int execute(JsonNode golden, List<JsonNode> cases, int repeat, int workers, double budget, String target,
                              CaseRunner runner, Path outDir, PrintStream out) throws Exception {
        double min = golden.path("gate").path("min_pass_rate").asDouble(1.0);
        Map<String, ObjectNode> byId = new LinkedHashMap<>();
        for (JsonNode c : cases) {
            ObjectNode r = Contracts.object().put("id", c.path("id").asText());
            boolean crit = false;
            for (JsonNode chk : c.path("checks")) crit |= chk.path("critical").asBoolean();
            r.put("has_critical", crit);
            r.putArray("runs");
            byId.put(c.path("id").asText(), r);
        }
        double[] spent = {0};
        ExecutorService pool = Executors.newFixedThreadPool(Math.max(1, workers));
        List<Future<?>> jobs = new ArrayList<>();
        for (int rep = 0; rep < repeat; rep++) {
            for (JsonNode c : cases) {
                jobs.add(pool.submit(() -> {
                    synchronized (spent) { if (spent[0] > budget) return; }
                    ObjectNode r = runner.run(c);
                    if (acceptedRefusal(c, r)) r = accept(r, c);
                    if (r.has("error")) {                                  // an ERROR is retried once; a FAIL never is
                        synchronized (spent) { spent[0] += r.path("cost_usd").asDouble(); }
                        out.printf("  RETRY %-26s after: %s%n", c.path("id").asText(), Tools.cut(r.path("error").asText(), 70));
                        ObjectNode again = runner.run(c);
                        if (acceptedRefusal(c, again)) again = accept(again, c);
                        again.put("retried_after", Tools.cut(r.path("error").asText(), 200)).put("errored_cost_usd", r.path("cost_usd").asDouble());
                        r = again;
                    }
                    if (!r.has("error") && !r.has("grade")) r.set("grade", Checks.gradeCase(c, r));
                    synchronized (spent) {
                        spent[0] += r.path("cost_usd").asDouble();
                        ((ArrayNode) byId.get(c.path("id").asText()).get("runs")).add(r);
                        String st = r.has("error") ? "ERROR" : r.path("grade").path("accepted_refusal").asBoolean() ? "PASS*"
                                : r.path("grade").path("passed").asBoolean() ? "PASS" : "FAIL";
                        out.printf("  %-5s %-26s $%.3f  %s%n", st, c.path("id").asText(), r.path("cost_usd").asDouble(), r.path("status").asText(""));
                    }
                }));
            }
        }
        for (Future<?> f : jobs) f.get();
        pool.shutdown();
        ArrayNode results = Contracts.JSON.createArrayNode();
        byId.values().forEach(results::add);
        ObjectNode gate = Checks.gate(results, min);
        if (spent[0] > budget) gate.put("budget_exceeded", true);
        ObjectNode doc = Contracts.object().put("suite", golden.path("suite").asText()).put("target", target)
                .put("cost_usd", Math.round(spent[0] * 10000) / 10000.0);
        doc.set("gate", gate);
        doc.set("cases", results);
        String stamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"));
        Files.createDirectories(outDir);
        Path json = outDir.resolve("eval-" + target + "-" + stamp + ".json"), md = outDir.resolve("eval-" + target + "-" + stamp + ".md");
        // secrets masked on save, not truncated (--regrade needs the full text)
        Files.writeString(json, Contracts.JSON.writerWithDefaultPrettyPrinter().writeValueAsString(
                Spans.redact(Contracts.JSON.convertValue(doc, Map.class), 0)), StandardCharsets.UTF_8);
        String report = report(doc);
        Files.writeString(md, report, StandardCharsets.UTF_8);
        out.println();
        out.println(report);
        out.println("results: " + json);
        if (gate.path("runs").asInt() == gate.path("unrecovered_errors").asInt()) return 2;
        return gate.path("ok").asBoolean() ? 0 : 1;
    }

    /** The case accepts this run's refusal: {"accept_refusal": {"status": "guardrail_intervened"}} and the run stopped that way. */
    static boolean acceptedRefusal(JsonNode kase, JsonNode r) {
        String want = kase.path("accept_refusal").path("status").asText("");
        if (want.isEmpty()) return false;
        return want.equals(r.path("status").asText()) || r.path("error").asText("").startsWith(want + ":");
    }

    /** A refusal the case accepts: passed, graded without checks, and marked so the report can count it separately. */
    static ObjectNode accept(ObjectNode r, JsonNode kase) {
        ObjectNode out = r.deepCopy();
        String why = out.has("error") ? out.remove("error").asText() : out.path("status").asText();
        out.put("status", kase.path("accept_refusal").path("status").asText()).put("refusal", why);
        ObjectNode g = out.putObject("grade").put("passed", true).put("accepted_refusal", true);
        g.putArray("checks");
        return out;
    }

    /** Re-grade a saved results file with the current golden checks: no model, no cost. */
    public static int regrade(JsonNode golden, Path saved, PrintStream out) throws Exception {
        JsonNode prev = Contracts.JSON.readTree(saved.toFile());
        Map<String, JsonNode> byId = new LinkedHashMap<>();
        golden.path("cases").forEach(c -> byId.put(c.path("id").asText(), c));
        ArrayNode results = Contracts.JSON.createArrayNode();
        for (JsonNode c : prev.path("cases")) {
            JsonNode kase = byId.get(c.path("id").asText());
            if (kase == null) continue;
            ObjectNode cc = ((ObjectNode) c).deepCopy();
            ArrayNode runs = cc.putArray("runs");
            for (JsonNode r : c.path("runs")) {
                ObjectNode rr = ((ObjectNode) r).deepCopy();
                if (acceptedRefusal(kase, rr)) rr = accept(rr, kase);
                else if (!rr.has("error")) rr.set("grade", Checks.gradeCase(kase, rr));
                runs.add(rr);
            }
            results.add(cc);
        }
        ObjectNode doc = Contracts.object().put("suite", golden.path("suite").asText() + " (re-graded)")
                .put("target", prev.path("target").asText()).put("cost_usd", 0.0);
        ObjectNode gate = Checks.gate(results, golden.path("gate").path("min_pass_rate").asDouble(1.0));
        doc.set("gate", gate);
        doc.set("cases", results);
        out.println(report(doc));
        return gate.path("ok").asBoolean() ? 0 : 1;
    }

    static String report(JsonNode doc) {
        JsonNode g = doc.path("gate");
        StringBuilder sb = new StringBuilder();
        sb.append("## Eval gate: ").append(g.path("ok").asBoolean() ? "PASS" : "FAIL").append(" - ").append(doc.path("suite").asText())
          .append(" (target: ").append(doc.path("target").asText()).append(")\n\n");
        sb.append(String.format("%d/%d runs passed (%.0f%%, need %.0f%%) · first attempt %d/%d · %d retried after an error · "
                        + "%d unrecovered errors%s · $%.2f%n%n", g.path("passed").asInt(), g.path("runs").asInt(), 100 * g.path("pass_rate").asDouble(),
                100 * g.path("min_pass_rate").asDouble(), g.path("first_attempt_passed").asInt(), g.path("runs").asInt(),
                g.path("retried").asInt(), g.path("unrecovered_errors").asInt(),
                g.path("accepted_refusals").asInt() > 0 ? " · " + g.path("accepted_refusals").asInt() + " accepted as a guardrail refusal" : "",
                doc.path("cost_usd").asDouble()));
        sb.append("| case | runs passed | failing checks |\n|---|---|---|\n");
        for (JsonNode c : doc.path("cases")) {
            int ok = 0, n = c.path("runs").size();
            TreeSet<String> fails = new TreeSet<>();
            for (JsonNode r : c.path("runs")) {
                if (r.has("error")) { fails.add("error: " + Tools.cut(r.path("error").asText(), 80)); continue; }
                if (r.path("grade").path("accepted_refusal").asBoolean()) fails.add("accepted refusal: " + r.path("status").asText());
                if (r.path("grade").path("passed").asBoolean()) ok++;
                for (JsonNode ch : r.path("grade").path("checks")) {
                    if (!ch.path("passed").asBoolean()) {
                        String name = ch.path("critical").asBoolean() ? "**" + ch.path("check").asText() + "**" : ch.path("check").asText();
                        fails.add(name + ": " + ch.path("detail").asText());
                    }
                }
            }
            sb.append("| ").append(c.path("id").asText()).append(" | ").append(ok).append("/").append(n)
              .append(ok > 0 && ok < n ? " (flaky)" : "").append(" | ").append(fails.isEmpty() ? "—" : String.join("<br>", fails)).append(" |\n");
        }
        if (g.path("critical_failures").size() > 0) {
            sb.append("\n**Critical checks failed** - a safety property is never averaged away:\n");
            g.path("critical_failures").forEach(f -> sb.append("- ").append(f.asText()).append("\n"));
        }
        return sb.toString();
    }

    /** The local target: one private aira-ops, a read token per account, the training gateway for the model. */
    public static CaseRunner local(PrivateOps ops, Function<Double, ResponderAgent> agents, double perRunBudget) {
        return kase -> {
            String id = kase.path("id").asText();
            String rid = "eval-" + id + "-" + PrivateOps.hex(3);
            Spans tr = new Spans("capstone", rid);
            long t0 = System.nanoTime();
            try {
                String acc = kase.path("account").asText();
                OpsReader reader = new HttpOpsReader(ops.url, ops.readTokens.get(acc));
                Responder.Outcome o = Responder.run(rid, acc, Sla.parseInstant(kase.path("as_of").asText()),
                        kase.path("question").asText(""), reader, agents.apply(perRunBudget), tr);
                ObjectNode r = o.toJson();
                r.remove("sla");
                r.put("seconds", Math.round((System.nanoTime() - t0) / 1e8) / 10.0).put("trace", tr.path.getFileName().toString());
                if (o.status().equals("failed") || o.status().equals("guardrail_intervened")) {
                    ObjectNode e = Contracts.object().put("error", o.error()).put("status", o.status()).put("cost_usd", o.costUsd())
                            .put("trace", tr.path.getFileName().toString());
                    return e;
                }
                return r;
            } catch (RuntimeException e) {
                return Contracts.object().put("error", e.getClass().getSimpleName() + ": " + Tools.cut(String.valueOf(e.getMessage()), 300))
                        .put("cost_usd", 0.0).put("trace", tr.path.getFileName().toString());
            }
        };
    }

    public static List<String> accounts(List<JsonNode> cases) {
        TreeSet<String> s = new TreeSet<>();
        cases.forEach(c -> s.add(c.path("account").asText()));
        return new ArrayList<>(s);
    }

    /** The real model for local mode: labkit Config (.env) -> GatewayClient -> common's ModelClient. */
    public static Function<Double, ResponderAgent> localAgents(ModelClient model, String pricingModel, int maxTurns) {
        return budget -> new ResponderAgent(model, pricingModel, maxTurns, budget, System::getenv, Telemetry.NONE);
    }
}
