package com.airamatrix.day4.lab52;

import java.io.IOException;
import java.io.PrintStream;
import java.net.HttpURLConnection;
import java.net.ServerSocket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;

import com.airamatrix.day4.common.AgentRunner;
import com.airamatrix.day4.common.AgentRunner.AgentResult;
import com.airamatrix.day4.common.AgentRunner.ToolCall;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.Spans;
import com.fasterxml.jackson.core.util.DefaultIndenter;
import com.fasterxml.jackson.core.util.DefaultPrettyPrinter;
import com.fasterxml.jackson.core.util.Separators;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectWriter;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

/** The pieces of run_evals.py: a throwaway aira-ops, one graded run, and the report. */
public final class EvalHarness {
    private EvalHarness() {}

    /** json.dumps(indent=2)-style output for results files. */
    static final ObjectWriter PRETTY;
    static {
        DefaultPrettyPrinter pp = new DefaultPrettyPrinter(Separators.createDefaultInstance()
                .withObjectFieldValueSpacing(Separators.Spacing.AFTER)
                .withObjectEmptySeparator("").withArrayEmptySeparator(""));
        DefaultIndenter ind = new DefaultIndenter("  ", "\n");
        pp.indentObjectsWith(ind);
        pp.indentArraysWith(ind);
        PRETTY = Contracts.JSON.writer(pp);
    }

    private static final SecureRandom RNG = new SecureRandom();

    static String tokenHex(int bytes) {
        byte[] b = new byte[bytes];
        RNG.nextBytes(b);
        return HexFormat.of().formatHex(b);
    }

    /** What a run needs from aira-ops: where it is and a read-only caller token. */
    public interface OpsHandle extends AutoCloseable {
        String url();
        String readToken();
        @Override void close();
    }

    /** Makes the agent runner for one run (the live one is GatewayAgentRunner; tests pass a fake). */
    @FunctionalInterface
    public interface RunnerFactory {
        AgentRunner create(String opsUrl, String readToken, double maxBudgetUsd);
    }

    static int freePort() throws IOException {
        try (ServerSocket s = new ServerSocket(0, 0, java.net.InetAddress.getLoopbackAddress())) {
            return s.getLocalPort();
        }
    }

    /**
     * A throwaway aira-ops with a read-only caller token for the agent - the same two commands the
     * Python harness runs: {@code aira_ops.py --callers F --issue-token eval-agent}, then
     * {@code aira_ops.py --port P --quiet --reset --db D --callers F}. Fresh data, private port.
     */
    public static final class Ops implements OpsHandle {
        private final Path tmp;
        private final Process p;
        private final String url, readToken;
        private final Thread hook;

        private Ops(Path tmp, Process p, String url, String readToken) {
            this.tmp = tmp; this.p = p; this.url = url; this.readToken = readToken;
            this.hook = new Thread(p::destroy);
            Runtime.getRuntime().addShutdownHook(hook);   // Ctrl-C must not leave a server behind
        }

        /** python: python3 (python on Windows) unless LAB_PYTHON says otherwise. childEnv: already scrubbed. */
        public static Ops start(String python, Path opsScript, Map<String, String> childEnv) throws Exception {
            Path tmp = Files.createTempDirectory("lab52-eval-");
            Process server = null;
            try {
                String admin = "eval-admin-" + tokenHex(8);
                Path callers = tmp.resolve("callers.json");
                ProcessBuilder issue = new ProcessBuilder(python, opsScript.toString(), "--callers", callers.toString(),
                        "--issue-token", "eval-agent").redirectError(ProcessBuilder.Redirect.DISCARD);
                env(issue, childEnv, admin);
                Process ip = issue.start();
                String token = new String(ip.getInputStream().readAllBytes(), StandardCharsets.UTF_8).strip();
                if (ip.waitFor() != 0 || token.isEmpty()) {
                    throw new IllegalStateException("aira-ops --issue-token failed (exit " + ip.exitValue() + ")");
                }
                int port = freePort();
                String url = "http://127.0.0.1:" + port;
                ProcessBuilder pb = new ProcessBuilder(python, opsScript.toString(), "--port", String.valueOf(port), "--quiet",
                        "--reset", "--db", tmp.resolve("ops.sqlite").toString(), "--callers", callers.toString())
                        .redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD);
                env(pb, childEnv, admin);
                server = pb.start();
                for (int i = 0; i < 60; i++) {
                    if (healthy(url)) return new Ops(tmp, server, url, token);
                    Thread.sleep(100);
                }
                throw new IllegalStateException("aira-ops did not start");
            } catch (Exception e) {
                if (server != null) server.destroy();
                deleteTree(tmp);
                throw e;
            }
        }

        private static void env(ProcessBuilder pb, Map<String, String> childEnv, String admin) {
            pb.environment().clear();
            pb.environment().putAll(childEnv);
            pb.environment().put("AIRA_OPS_TOKEN", admin);
        }

        private static boolean healthy(String url) {
            try {
                HttpURLConnection c = (HttpURLConnection) URI.create(url + "/health").toURL().openConnection();
                c.setConnectTimeout(300);
                c.setReadTimeout(300);
                int code = c.getResponseCode();
                c.disconnect();
                return code >= 200 && code < 400;
            } catch (IOException e) {
                return false;
            }
        }

        @Override public String url() { return url; }
        @Override public String readToken() { return readToken; }

        @Override
        public void close() {
            p.destroy();
            try { p.waitFor(10, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            if (p.isAlive()) p.destroyForcibly();
            try { Runtime.getRuntime().removeShutdownHook(hook); } catch (IllegalStateException ignored) { /* shutting down */ }
            deleteTree(tmp);
        }
    }

    static void deleteTree(Path dir) {
        try (Stream<Path> s = Files.walk(dir)) {
            s.sorted(Comparator.reverseOrder()).forEach(q -> q.toFile().delete());
        } catch (IOException ignored) { /* temp dir */ }
    }

    /** run_one(): run the investigate stage for one case, validate, trace, grade. Never throws. */
    public static ObjectNode runOne(JsonNode kase, OpsHandle ops, double maxBudget, RunnerFactory factory) {
        String id = kase.get("id").asText();
        Spans tr = new Spans("lab5-2", id + "-" + tokenHex(3));
        long t0 = System.nanoTime();
        ObjectNode out = Contracts.object();
        try {
            AgentResult r;
            List<Boolean> oks;
            try (Spans.Span sp = tr.span("eval.case", Map.of("case", id))) {
                try {
                    r = factory.create(ops.url(), ops.readToken(), maxBudget).run("investigate", Agents.INVESTIGATE_SYSTEM,
                            Agents.investigatePrompt(kase.get("account").asText(), kase.get("question").asText()), Contracts.PROPOSAL);
                    Contracts.checkChange(Contracts.validate(r.output(), Contracts.PROPOSAL).get("proposed_change"));
                    oks = r.toolOk() == null || r.toolOk().isEmpty()
                            ? r.toolCalls().stream().map(t -> (Boolean) null).toList() : r.toolOk();
                    for (int i = 0; i < r.toolCalls().size(); i++) {
                        ToolCall call = r.toolCalls().get(i);
                        Map<String, Object> attrs = new LinkedHashMap<>();
                        attrs.put("tool", call.name());
                        attrs.put("input", Contracts.JSON.convertValue(call.input(), Map.class));
                        attrs.put("ok", i < oks.size() ? oks.get(i) : null);
                        tr.event("tool_call", attrs);
                    }
                    sp.set("cost_usd", Py.round(r.costUsd(), 4)).set("tool_calls", r.toolCalls().size());
                } catch (RuntimeException e) {
                    sp.fail(e);
                    throw e;
                }
            }
            ObjectNode raw = Contracts.object();
            raw.set("output", r.output());
            ArrayNode calls = raw.putArray("tool_calls");
            for (int i = 0; i < r.toolCalls().size(); i++) {
                ArrayNode c = calls.addArray().add(r.toolCalls().get(i).name());
                c.add(r.toolCalls().get(i).input());
                Boolean ok = i < oks.size() ? oks.get(i) : null;
                if (ok == null) c.addNull(); else c.add(ok);
            }
            out.set("raw", raw);
            out.set("grade", Graders.gradeCase(kase, raw));
            out.put("cost_usd", r.costUsd());
        } catch (Exception e) {
            String msg = e.getMessage() == null ? "" : e.getMessage();
            out.put("error", e.getClass().getSimpleName() + ": " + Py.head(msg, 300));
            out.put("cost_usd", e instanceof AgentRunner.RunnerException re ? re.costUsd : 0.0);
        }
        out.put("seconds", Py.round((System.nanoTime() - t0) / 1e9, 1));
        out.put("trace", tr.path.getFileName().toString());
        return out;
    }

    /** report(): the Markdown the terminal and the GitHub job summary both get. */
    public static String report(String suite, List<? extends JsonNode> caseResults, JsonNode g, double totalCost,
                                PrintStream out, String stepSummary) {
        List<String> lines = new ArrayList<>();
        int runs = g.get("runs").asInt();
        lines.add("## Eval gate: " + (g.get("ok").asBoolean() ? "PASS" : "FAIL") + " — " + suite);
        lines.add("");
        lines.add(g.get("passed").asInt() + "/" + runs + " runs passed (" + Py.pct(g.get("pass_rate").asDouble()) + ", need "
                + Py.pct(g.get("min_pass_rate").asDouble()) + ") · first attempt " + g.get("first_attempt_passed").asInt() + "/" + runs
                + " · " + g.get("retried").asInt() + " retried after an error · " + g.get("unrecovered_errors").asInt()
                + " unrecovered errors · $" + Py.fixed(totalCost, 2));
        lines.add("");
        lines.add("A run = one attempt + at most one retry for an execution/schema error. An unrecovered error "
                + "or a failed critical check blocks the gate.");
        lines.add("");
        lines.add("| case | runs passed | failing checks |");
        lines.add("|---|---|---|");
        for (JsonNode c : caseResults) {
            JsonNode cruns = c.path("runs");
            int ok = 0;
            TreeSet<String> fails = new TreeSet<>();
            for (JsonNode r : cruns) {
                if (r.path("grade").path("passed").asBoolean(false)) ok++;
                for (JsonNode ch : r.path("grade").path("checks")) {
                    if (ch.path("passed").asBoolean()) continue;
                    String star = ch.path("critical").asBoolean() ? "**" : "";
                    fails.add(star + ch.path("check").asText() + star + ": " + ch.path("detail").asText());
                }
                if (r.has("error")) fails.add("error: " + Py.head(r.get("error").asText(), 80));
            }
            String flaky = (ok > 0 && ok < cruns.size()) ? " (flaky)" : "";
            lines.add("| " + c.path("id").asText() + " | " + ok + "/" + cruns.size() + flaky + " | "
                    + (fails.isEmpty() ? "—" : String.join("<br>", fails)) + " |");
        }
        if (!g.path("critical_failures").isEmpty()) {
            lines.add("");
            lines.add("**Critical checks failed** — a safety property is never averaged away:");
            for (JsonNode cf : g.get("critical_failures")) lines.add("- `" + cf.get(0).asText() + "`: " + cf.get(1).asText());
        }
        String md = String.join("\n", lines);
        out.println(md);
        out.flush();
        if (stepSummary != null && !stepSummary.isEmpty()) {
            try {
                Files.writeString(Path.of(stepSummary), md + "\n", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException e) {
                System.err.println("could not write GITHUB_STEP_SUMMARY: " + e.getMessage());
            }
        }
        return md;
    }

    /** Results are kept for --regrade: secrets masked (not truncated - the grader needs the full text). */
    static void writeResults(Path file, JsonNode results) throws IOException {
        Object plain = Contracts.JSON.convertValue(results, Object.class);
        Files.createDirectories(file.getParent());
        Files.writeString(file, PRETTY.writeValueAsString(Spans.redact(plain, 0)), StandardCharsets.UTF_8);
    }
}
