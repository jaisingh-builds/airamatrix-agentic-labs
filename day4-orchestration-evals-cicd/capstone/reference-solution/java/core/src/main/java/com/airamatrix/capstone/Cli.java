package com.airamatrix.capstone;

import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.airamatrix.labkit.Config;
import com.airamatrix.labkit.GatewayClient;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * Local mode - your own aira-ops, the training gateway, no AWS.
 *
 *   java -jar core/target/capstone-cli.jar tokens --account ACC-1001          read token (agent) + apply token (human), shown once
 *   java -jar core/target/capstone-cli.jar run --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question "..."]
 *   java -jar core/target/capstone-cli.jar show RUN | list | trace RUN
 *   java -jar core/target/capstone-cli.jar approve RUN --by "Name" --reason "why"      (or reject)
 *   java -jar core/target/capstone-cli.jar apply RUN                                   (its own shell: AIRA_OPS_APPLY_TOKEN)
 *   java -jar core/target/capstone-cli.jar replay fixtures/blocked-leak.json           (a saved proposal through the guardrail, $0)
 *   java -jar core/target/capstone-cli.jar eval [--repeat 2] [--cases a,b] [--budget 1.5] | eval --regrade results/x.json
 *
 * Environment: AIRA_OPS_URL (default http://127.0.0.1:8150), AIRA_OPS_READ_TOKEN (run/replay), AIRA_OPS_APPLY_TOKEN (apply
 * only), CAPSTONE_DB (default java/out/capstone-runs.sqlite), LAB_TRACE_DIR (default <repo>/traces), and .env for the gateway.
 */
public final class Cli {
    public static void main(String[] argv) {
        System.exit(run(Arrays.asList(argv), System.getenv(), System.out, System.err));
    }

    public static int run(List<String> args, Map<String, String> env, PrintStream out, PrintStream err) {
        if (args.isEmpty() || args.get(0).startsWith("-h")) { out.println(usage()); return args.isEmpty() ? 2 : 0; }
        Opts o = Opts.parse(args.subList(1, args.size()));
        String url = env.getOrDefault("AIRA_OPS_URL", "http://127.0.0.1:8150");
        try {
            switch (args.get(0)) {
                case "tokens" -> { return tokens(o, out); }
                case "eval" -> { return eval(o, out); }
                default -> { }
            }
            try (Store store = new Store(env.getOrDefault("CAPSTONE_DB", defaultDb()))) {
                switch (args.get(0)) {
                    case "run" -> {
                        String read = need(env, "AIRA_OPS_READ_TOKEN", "the agent's read-only token (capstone tokens)");
                        String acc = o.need("account");
                        OffsetDateTime asOf = o.has("as-of") ? Sla.parseInstant(o.get("as-of")) : OffsetDateTime.now();
                        String rid = store.createRun(Store.newId(), acc, Sla.iso(asOf), o.get("question"), "local");
                        Spans tr = new Spans("capstone", rid);
                        out.println("run " + rid + " started · trace " + tr.path);
                        Config cfg = new Config();
                        GatewayClient gw = new GatewayClient(cfg);
                        ResponderAgent agent = new ResponderAgent(gw::messages, cfg.model, o.intOr("max-turns", Math.max(cfg.maxSteps, 10)),
                                o.dblOr("budget", cfg.budgetUsd), System::getenv, Telemetry.NONE);
                        Responder.Outcome res = Responder.run(rid, acc, asOf, o.get("question"), new HttpOpsReader(url, read), agent, tr);
                        Responder.save(store, res, tr.path.toString());
                        show(store, rid, out);
                        return switch (res.status()) { case "blocked" -> 3; case "failed", "guardrail_intervened" -> 1; default -> 0; };
                    }
                    case "replay" -> {
                        String read = need(env, "AIRA_OPS_READ_TOKEN", "a read-only token: the guardrail verifies against live data");
                        JsonNode fx = Contracts.JSON.readTree(Paths.get(o.pos(0)).toFile());
                        String rid = replay(store, fx, new HttpOpsReader(url, read));
                        out.println("run " + rid + " replayed from " + o.pos(0) + " (no model, $0)");
                        show(store, rid, out);
                        return store.run(rid).status().equals("blocked") ? 3 : 0;
                    }
                    case "show" -> { show(store, o.pos(0), out); return 0; }
                    case "list" -> {
                        for (Store.Run r : store.runs(20)) {
                            out.printf("%s  %-6s %-9s %-19s $%-7.4f %s%n", r.id(), r.mode(), r.accountId(), r.status(), r.costUsd(), r.asOf());
                        }
                        return 0;
                    }
                    case "trace" -> { TraceView.render(Paths.get(store.run(o.pos(0)).trace()), out); return 0; }
                    case "approve", "reject" -> {
                        Store.Run r = store.run(o.pos(0));
                        Gate.decide(store, r.id(), args.get(0), o.get("by"), "os:" + System.getProperty("user.name"), o.get("reason"),
                                traceFor(r));
                        show(store, r.id(), out);
                        return 0;
                    }
                    case "apply" -> {
                        String write = need(env, "AIRA_OPS_APPLY_TOKEN", "the apply step's own write token (capstone tokens)");
                        Store.Run r = store.run(o.pos(0));
                        Gate.apply(store, r.id(), new Gate.HttpOpsWriter(url, write, Duration.ofSeconds(8)), url, traceFor(r));
                        show(store, r.id(), out);
                        return store.run(r.id()).status().equals("applied") ? 0 : 1;
                    }
                    default -> { err.println("unknown command " + args.get(0) + "\n" + usage()); return 2; }
                }
            }
        } catch (Gate.GateError | IllegalArgumentException e) {
            err.println("refused: " + e.getMessage());
            return 3;
        } catch (IllegalStateException e) {
            err.println(e.getMessage());
            return 2;
        } catch (Exception e) {
            err.println(e.getClass().getSimpleName() + ": " + Spans.redact(String.valueOf(e.getMessage())));
            return 1;
        }
    }

    /** java/out/ (gitignored): the store, the callers file and your aira-ops database never land in a tracked folder. */
    public static String defaultDb() {
        Path out = Repo.java().resolve("out");
        try { Files.createDirectories(out); } catch (Exception e) { throw new IllegalStateException(e); }
        return out.resolve("capstone-runs.sqlite").toString();
    }

    static Spans traceFor(Store.Run r) {
        return new Spans("capstone", r.id());     // same file as the run, as long as LAB_TRACE_DIR is the same
    }

    /** A saved proposal through the SAME guardrail and gate - every learner gets the refusal on demand. */
    public static String replay(Store store, JsonNode fx, OpsReader ops) {
        String acc = fx.path("account").asText();
        OffsetDateTime asOf = Sla.parseInstant(fx.path("as_of").asText());
        String rid = store.createRun(Store.newId(), acc, Sla.iso(asOf), fx.path("question").asText() + " [replay of " + fx.path("source").asText() + "]", "local");
        Spans tr = new Spans("capstone", rid);
        Sla.Report sla;
        Guardrails.Verdict v;
        try (Spans.Span root = tr.span("replay", Map.of("account", acc, "stage", "sla-responder"))) {
            try (Spans.Span vs = tr.span("guardrail.verify", Map.of("stage", "code-guardrail"))) {
                sla = Sla.compute(ops, acc, asOf);
                v = Guardrails.verify(fx.path("proposal"), sla);
                vs.set("verdict", v.passed() ? "pass" : "block").set("denials", v.rules());
                if (!v.passed()) vs.fail("guardrail refused: " + String.join(", ", v.rules()));
            }
            root.set("verdict", v.passed() ? "pass" : "blocked");
        }
        String action = fx.path("proposal").path("action").path("type").asText();
        String status = !v.passed() ? "blocked" : action.equals("none") ? "no_action" : "awaiting_approval";
        store.finishRun(rid, status, 0, 0, 0, null, tr.path.toString());
        store.saveProposal(rid, fx.path("proposal"), sla.toJson(), v.toJson(), Contracts.JSON.createArrayNode());
        return rid;
    }

    public static void show(Store store, String rid, PrintStream out) {
        Store.Run r = store.run(rid);
        out.printf("run %s · %s · %s · as_of %s · %s · $%.4f · %d turns · %d tool calls%n", r.id(), r.mode(), r.accountId(), r.asOf(),
                r.status(), r.costUsd(), r.turns(), r.toolCalls());
        if (r.question() != null) out.println("  request: " + r.question());
        if (r.error() != null) out.println("  error:   " + r.error());
        Store.ProposalRow p = store.proposal(rid);
        if (p != null) {
            if (p.sla() != null) {
                out.println("\n[sla] computed by code at as_of:");
                for (JsonNode i : p.sla().path("items")) {
                    out.printf("  %-7s %-7s %-12s %5d / %-5d min  %3d%%  %s%n", i.path("item").asText(), i.path("kind").asText(),
                            i.path("status").asText(), i.path("elapsed_minutes").asLong(), i.path("target_minutes").asLong(),
                            i.path("pct_of_target").asInt(), i.path("state").asText());
                }
            }
            JsonNode pr = p.proposal();
            out.println("\n[proposal]");
            out.println("  summary: " + pr.path("summary").asText());
            out.println("  cause:   " + pr.path("likely_cause").asText());
            List<String> ex = new ArrayList<>();
            pr.path("exposed").forEach(e -> ex.add(e.path("item").asText() + " " + e.path("state").asText()));
            out.println("  exposed: " + (ex.isEmpty() ? "(none)" : String.join(", ", ex)));
            if (pr.path("untrusted_instructions_seen").size() > 0) out.println("  flagged: " + pr.path("untrusted_instructions_seen") + " (instructions in ticket text - not followed)");
            JsonNode a = pr.path("action");
            out.println("  action:  " + a.path("type").asText() + (a.has("ticket_id") ? " on " + a.path("ticket_id").asText() : "") + " - " + a.path("reason").asText());
            if (a.has("comment")) out.println("  comment (customer-visible):\n    " + a.path("comment").asText().replace("\n", "\n    "));
            if (p.verdict() != null) {
                Guardrails.Verdict v = Guardrails.Verdict.fromJson(p.verdict());
                out.println("\n[guardrail] " + (v.passed() ? "PASS - every rule" : "BLOCKED"));
                v.denials().forEach(d -> out.println("  x " + d.rule() + ": " + d.detail()));
            }
        }
        Store.Approval ap = store.approval(rid);
        if (ap != null) out.println("\n[gate] " + ap.decision() + " by " + ap.approver() + " (" + ap.principal() + ") at " + ap.at() + ": " + ap.reason());
        Store.Operation op = store.operation(rid);
        if (op != null) out.println("\n[apply] " + op.status() + " · op " + op.opId() + " · " + op.response());
        if (r.status().equals("awaiting_approval")) out.println("\nnext: approve " + rid + " --by \"Your Name\" --reason \"why\"   (or reject)");
        if (r.status().equals("approved") && "local".equals(r.mode())) out.println("\nnext: apply " + rid + "   (in the shell that holds AIRA_OPS_APPLY_TOKEN)");
    }

    static int tokens(Opts o, PrintStream out) throws Exception {
        String acc = o.need("account");
        Path callers = Paths.get(o.getOr("callers", Repo.java().resolve("out/capstone-callers.json").toString())).toAbsolutePath();
        Files.createDirectories(callers.getParent());
        String admin = "unused-" + PrivateOps.hex(4);    // --issue-token only edits the callers file
        String read = PrivateOps.issue(callers, admin, "sla-responder", acc, false);
        String write = PrivateOps.issue(callers, admin, "capstone-apply", acc, true);
        out.println("# Shown once; " + callers.getFileName() + " keeps only their SHA-256. Both are scoped to " + acc + ".");
        out.println("export AIRA_OPS_READ_TOKEN=" + read + "     # shell 1: the agent (read-only)");
        out.println("export AIRA_OPS_APPLY_TOKEN=" + write + "    # shell 2: apply, the human's step - never in shell 1");
        out.println("# PowerShell: $env:AIRA_OPS_READ_TOKEN=\"" + read + "\"  /  $env:AIRA_OPS_APPLY_TOKEN=\"" + write + "\"");
        out.println("# start YOUR aira-ops with this callers file and your own db and port, e.g.:");
        out.println("#   python3 \"" + Repo.opsScript() + "\" --port 8177 --db \"" + callers.getParent().resolve("capstone-ops.sqlite")
                + "\" --callers \"" + callers + "\" --reset");
        return 0;
    }

    static int eval(Opts o, PrintStream out) throws Exception {
        Path goldenPath = Paths.get(o.getOr("golden", Repo.solution().resolve("golden/cases.json").toString()));
        JsonNode golden = Evals.load(goldenPath);
        if (o.has("regrade")) return Evals.regrade(golden, Paths.get(o.get("regrade")), out);
        List<JsonNode> cases = Evals.select(golden, o.get("cases"));
        if (cases.isEmpty()) { out.println("no matching cases"); return 2; }
        Config cfg;
        try { cfg = new Config().require(); } catch (IllegalStateException e) { out.println("setup: " + e.getMessage()); return 2; }
        for (String k : ResponderAgent.FORBIDDEN_ENV) {
            if (System.getenv(k) != null && !System.getenv(k).isEmpty()) { out.println("setup: unset " + k + " - evals start agents"); return 2; }
        }
        GatewayClient gw = new GatewayClient(cfg);
        try (PrivateOps ops = PrivateOps.start(Evals.accounts(cases), false)) {
            return Evals.execute(golden, cases, o.intOr("repeat", 1), o.intOr("workers", 3), o.dblOr("budget", 1.5), "local",
                    Evals.local(ops, Evals.localAgents(gw::messages, cfg.model, o.intOr("max-turns", 10)), o.dblOr("per-run-budget", 0.30)),
                    Paths.get(o.getOr("out", Repo.java().resolve("results").toString())), out);
        }
    }

    static String need(Map<String, String> env, String k, String what) {
        String v = env.get(k);
        if (v == null || v.isBlank()) throw new IllegalStateException(k + " is not set - " + what);
        return v;
    }

    static String usage() {
        return """
            java -jar core/target/capstone-cli.jar <command>      (local mode: your aira-ops + the training gateway)
              tokens  --account ACC-1001 [--callers FILE]
              run     --account ACC-1001 --as-of 2026-09-24T10:30:00+05:30 [--question TEXT] [--budget USD] [--max-turns N]
              show RUN | list | trace RUN
              approve RUN --by NAME --reason WHY        reject RUN --by NAME --reason WHY
              apply   RUN                                (needs AIRA_OPS_APPLY_TOKEN; starts no agent)
              replay  FIXTURE.json                       (a saved proposal through the guardrail; no model)
              eval    [--repeat N] [--cases a,b] [--budget USD] [--golden F] | eval --regrade RESULTS.json""";
    }

    /** --key value options and positional arguments. */
    record Opts(Map<String, String> kv, List<String> positional) {
        static Opts parse(List<String> a) {
            Map<String, String> kv = new HashMap<>();
            List<String> pos = new ArrayList<>();
            for (int i = 0; i < a.size(); i++) {
                if (a.get(i).startsWith("--")) {
                    if (i + 1 >= a.size()) throw new IllegalArgumentException(a.get(i) + " needs a value");
                    kv.put(a.get(i).substring(2), a.get(++i));
                } else pos.add(a.get(i));
            }
            return new Opts(kv, pos);
        }
        boolean has(String k) { return kv.containsKey(k); }
        String get(String k) { return kv.get(k); }
        String getOr(String k, String d) { return kv.getOrDefault(k, d); }
        String need(String k) { if (!has(k)) throw new IllegalArgumentException("--" + k + " is required"); return kv.get(k); }
        int intOr(String k, int d) { return has(k) ? Integer.parseInt(kv.get(k)) : d; }
        double dblOr(String k, double d) { return has(k) ? Double.parseDouble(kv.get(k)) : d; }
        String pos(int i) { if (positional.size() <= i) throw new IllegalArgumentException("missing RUN id / file"); return positional.get(i); }
    }
}
