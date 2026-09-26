package com.airamatrix.day4.lab51;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import com.airamatrix.day4.common.AgentRunner;
import com.airamatrix.day4.common.AgentRunner.RunnerException;
import com.airamatrix.day4.common.Contracts;
import com.airamatrix.day4.common.GatewayAgentRunner;
import com.airamatrix.day4.common.Spans;

/**
 * The command line - same subcommands, arguments, output and error messages as
 * lab5-1-handoff/pipeline.py:
 *
 * <pre>
 *   lab51 run --account ACC-1001 --question "Ingest backlog on T-1001"
 *   lab51 show RUN                   # every hand-off, as stored
 *   lab51 approve RUN --by "Jai" --reason "memory fix confirmed in INC-88" [--override]
 *   lab51 reject  RUN --by "Jai" --reason "..."
 *   lab51 apply   RUN                # only after approve; safe to re-run
 *   lab51 resume  RUN                # finish whatever is unfinished; never writes
 *   lab51 list
 *   lab51 replay fixtures/blocked-36cc478fce.json
 *   lab51 tokens --account ACC-1001
 * </pre>
 *
 * where {@code lab51} = {@value #PROG}, run from the repo root.
 */
public class Cli {

    public static final String PROG = "java -jar day4-orchestration-evals-cicd/java/lab51/target/lab51.jar";
    static final String TRACE_VIEW = "python3 day4-orchestration-evals-cicd/common/trace_view.py";

    /** Builds the agent runner for run/resume. The real one is GatewayAgentRunner; tests pass a fake. */
    public interface RunnerFactory {
        AgentRunner create(String opsUrl, String readToken);
    }

    public static RunnerFactory gatewayRunners() {
        return (opsUrl, readToken) -> GatewayAgentRunner.fromEnv(opsUrl, readToken, 0.40);
    }

    /** A SystemExit: message to stderr, then this exit code. */
    static final class Exit extends RuntimeException {
        final int code;
        Exit(int code, String message) { super(message); this.code = code; }
    }

    private final PrintStream out, err;
    private final Function<String, String> env;
    private final RunnerFactory runners;

    public Cli(PrintStream out, PrintStream err, Function<String, String> env, RunnerFactory runners) {
        this.out = out;
        this.err = err;
        this.env = env;
        this.runners = runners;
    }

    private String env(String name) {
        String v = env.apply(name);
        return v == null ? "" : v;
    }

    // --------------------------------------------------------------- argument parsing (argparse-alike)
    record Args(String cmd, Map<String, String> opts, List<String> pos, boolean override) {
        String opt(String k) { return opts.get(k); }
        String run() { return pos.isEmpty() ? null : pos.get(0); }
    }

    record Spec(List<String> required, List<String> positionals, boolean override, String usage) {}

    static final Map<String, Spec> SPECS = new LinkedHashMap<>();
    static {
        SPECS.put("run", new Spec(List.of("--account", "--question"), List.of(), false, "run [-h] --account ACCOUNT --question QUESTION"));
        SPECS.put("show", new Spec(List.of(), List.of("run"), false, "show [-h] run"));
        SPECS.put("apply", new Spec(List.of(), List.of("run"), false, "apply [-h] run"));
        SPECS.put("resume", new Spec(List.of(), List.of("run"), false, "resume [-h] run"));
        SPECS.put("approve", new Spec(List.of("--by", "--reason"), List.of("run"), true, "approve [-h] --by BY --reason REASON [--override] run"));
        SPECS.put("reject", new Spec(List.of("--by", "--reason"), List.of("run"), true, "reject [-h] --by BY --reason REASON [--override] run"));
        SPECS.put("list", new Spec(List.of(), List.of(), false, "list [-h]"));
        SPECS.put("replay", new Spec(List.of(), List.of("fixture"), false, "replay [-h] fixture"));
        SPECS.put("tokens", new Spec(List.of("--account"), List.of(), false, "tokens [-h] --account ACCOUNT"));
    }

    static final String TOP_USAGE = "usage: lab51 [-h] {" + String.join(",", SPECS.keySet()) + ",serve} ...";

    Args parse(String[] argv) {
        if (argv.length == 0) {
            throw new Exit(2, TOP_USAGE + "\nlab51: error: the following arguments are required: cmd");
        }
        if (argv[0].equals("-h") || argv[0].equals("--help")) {
            out.println(TOP_USAGE + "\n\nLab 5.1 pipeline (Java)\n\n  serve [--apply] [--port N]   REST API (see README)");
            throw new Exit(0, null);
        }
        String cmd = argv[0];
        Spec spec = SPECS.get(cmd);
        if (spec == null) {
            throw new Exit(2, TOP_USAGE + "\nlab51: error: argument cmd: invalid choice: '" + cmd + "' (choose from "
                    + String.join(", ", SPECS.keySet().stream().map(k -> "'" + k + "'").toList()) + ")");
        }
        String usage = "usage: lab51 " + spec.usage();
        Map<String, String> opts = new HashMap<>();
        List<String> pos = new ArrayList<>();
        List<String> unknown = new ArrayList<>();
        boolean override = false;
        for (int i = 1; i < argv.length; i++) {
            String a = argv[i];
            if (a.equals("-h") || a.equals("--help")) {
                out.println(usage);
                throw new Exit(0, null);
            }
            if (a.equals("--override") && spec.override()) { override = true; continue; }
            if (a.startsWith("--")) {
                String name = a, value = null;
                int eq = a.indexOf('=');
                if (eq > 0) { name = a.substring(0, eq); value = a.substring(eq + 1); }
                if (!spec.required().contains(name)) { unknown.add(a); continue; }
                if (value == null) {
                    if (i + 1 >= argv.length || argv[i + 1].startsWith("--")) {
                        throw new Exit(2, usage + "\nlab51 " + cmd + ": error: argument " + name + ": expected one argument");
                    }
                    value = argv[++i];
                }
                opts.put(name, value);
            } else if (pos.size() < spec.positionals().size()) {
                pos.add(a);
            } else {
                unknown.add(a);
            }
        }
        List<String> missing = new ArrayList<>();
        for (String r : spec.required()) if (!opts.containsKey(r)) missing.add(r);
        for (int i = pos.size(); i < spec.positionals().size(); i++) missing.add(spec.positionals().get(i));
        if (!missing.isEmpty()) {
            throw new Exit(2, usage + "\nlab51 " + cmd + ": error: the following arguments are required: " + String.join(", ", missing));
        }
        if (!unknown.isEmpty()) {
            throw new Exit(2, TOP_USAGE + "\nlab51: error: unrecognized arguments: " + String.join(" ", unknown));
        }
        return new Args(cmd, opts, pos, override);
    }

    // --------------------------------------------------------------- main
    /** Runs one command; returns the process exit code (0 ok, 1 refused/failed, 2 usage). */
    public int main(String[] argv) {
        try {
            dispatch(parse(argv));
            return 0;
        } catch (Exit e) {
            if (e.getMessage() != null) err.println(e.getMessage());
            return e.code;
        } catch (IllegalArgumentException | IllegalStateException e) {   // bad fixture path, missing .env, bad token...
            err.println("error: " + Spans.redact(String.valueOf(e.getMessage())));
            return 1;
        }
    }

    private void dispatch(Args a) {
        if (a.cmd().equals("tokens")) {
            issueTokens(a.opt("--account"));
            return;
        }
        Store store = new Store(dbPath(env));
        try {
            Pipeline p = new Pipeline(store);
            String opsUrl = Pipeline.opsUrl(env);
            // Least privilege per PROCESS. Only `apply` - which starts no agent - reads the write token.
            // Python drops the write/admin tokens from its environment before starting an agent (the SDK
            // passes the whole environment to the agent's subprocess). Java cannot remove a variable from
            // its own environment, so `run` and `resume` refuse to start instead - the same rule
            // GatewayAgentRunner enforces itself, checked here before a run row is created.
            String writeTok = a.cmd().equals("apply") ? env("AIRA_OPS_APPLY_TOKEN") : "";
            String readTok = env("AIRA_OPS_READ_TOKEN");       // handed to the read tools explicitly
            boolean startsAgents = a.cmd().equals("run") || a.cmd().equals("resume");
            if (startsAgents) {
                List<String> held = GatewayAgentRunner.FORBIDDEN_ENV.stream().filter(k -> !env(k).isEmpty()).toList();
                if (!held.isEmpty()) {
                    throw new Exit(1, "refusing to start the agents: " + String.join(", ", held) + " is set in this process. "
                            + "A process that runs agents holds no write or admin token - unset it in this shell "
                            + "(unset " + String.join(" ", held) + "  /  Remove-Item " + String.join(",", held.stream().map(k -> "Env:" + k).toList())
                            + ") and run `apply` from another shell.");
                }
                if (readTok.isEmpty()) {
                    throw new Exit(1, "AIRA_OPS_READ_TOKEN is not set - agents get a read-only caller token (" + PROG + " tokens)");
                }
            }
            String rid = null;
            try {
                switch (a.cmd()) {
                    case "run" -> {
                        AgentRunner runner = runners.create(opsUrl, readTok);
                        rid = store.createRun(a.opt("--account"), a.opt("--question"));
                        out.println("run " + rid + " started");
                        p.advance(runner, rid);
                        show(store, rid);
                    }
                    case "show" -> show(store, a.run());
                    case "approve", "reject" -> {
                        p.decide(a.run(), a.cmd(), a.opt("--by"), a.opt("--reason"), a.override());
                        show(store, a.run());
                    }
                    case "apply" -> {
                        if (writeTok.isEmpty()) {
                            throw new Exit(1, "AIRA_OPS_APPLY_TOKEN is not set - the apply step has its own credential (" + PROG + " tokens)");
                        }
                        p.apply(a.run(), writeTok, opsUrl);
                        show(store, a.run());
                    }
                    case "resume" -> {
                        p.resume(runners.create(opsUrl, readTok), a.run());
                        show(store, a.run());
                        if (List.of("approved", "outcome_unknown").contains(store.run(a.run()).status())) {
                            out.println("next: " + PROG + " apply " + a.run() + "   (in a shell that holds AIRA_OPS_APPLY_TOKEN)");
                        }
                    }
                    case "replay" -> {
                        String fixture = a.pos().get(0);
                        rid = p.replay(resolveFixture(fixture));
                        out.println("run " + rid + " replayed from " + fixture);
                        show(store, rid);
                    }
                    case "list" -> {
                        for (Store.RunRow r : store.runs()) {
                            String q = r.question() == null ? "" : r.question();
                            out.println(String.format("%s  %s  %-18s $%-7s %s", r.id(), r.accountId(), r.status(),
                                    PyJson.pyFloat(store.cost(r.id())), q.length() > 60 ? q.substring(0, 60) : q));
                        }
                    }
                    default -> throw new Exit(2, TOP_USAGE);
                }
            } catch (Pipeline.GateError | Contracts.ContractError | Store.Conflict e) {
                throw new Exit(1, "refused: " + e.getMessage());
            } catch (Store.NoSuchRun e) {
                throw new Exit(1, "refused: '" + e.getMessage() + "'");        // str(KeyError(...)) keeps the quotes
            } catch (RunnerException e) {
                String id = a.run() != null ? a.run() : rid;
                throw new Exit(1, "stage failed (recorded, finished stages kept): " + Spans.redact(e.getMessage()) + "\n"
                        + "  trace:  " + TRACE_VIEW + " --latest lab5-1-" + id + "\n"
                        + "  retry:  " + PROG + " resume " + id);
            }
        } finally {
            store.close();
        }
    }

    // --------------------------------------------------------------- show
    void show(Store store, String rid) {
        Store.RunRow r = store.run(rid);
        out.println("run " + rid + " · " + r.accountId() + " · " + r.status() + " · $" + PyJson.pyFloat(store.cost(rid)));
        out.println("  question: " + r.question());
        for (String name : List.of("investigate", "review")) {
            Store.StageRow s = store.stage(rid, name);
            if (s != null) {
                out.println("\n[" + name + "] " + s.status() + " · attempt " + s.attempt() + " · " + s.toolCalls()
                        + " tool calls · $" + PyJson.money(s.costUsd()));
                boolean hasOutput = s.output() != null && !(s.output().isContainerNode() && s.output().isEmpty());
                out.println(hasOutput ? "  " + PyJson.dumps(s.output(), 2).replace("\n", "\n  ")
                        : "  error: " + (s.error() == null ? "None" : s.error()));
            }
        }
        Store.ApprovalRow ap = store.approval(rid);
        if (ap != null) {
            out.println("\n[gate] " + ap.decision() + " by " + ap.approver() + (ap.override() != 0 ? " (OVERRIDE)" : "") + ": " + ap.reason());
        }
        Store.OperationRow op = store.operation(rid);
        if (op != null) {
            out.println("\n[apply] " + op.status() + " · op " + op.opId() + " · " + op.action() + " " + PyJson.dumps(op.payload()));
        }
    }

    // --------------------------------------------------------------- paths
    /** PIPELINE_DB, else lab51/runs.sqlite in the repo (next to this module's pom), else ./runs.sqlite. */
    static String dbPath(Function<String, String> env) {
        String v = env.apply("PIPELINE_DB");
        if (v != null && !v.isEmpty()) return v;
        Path module = Spans.repoRoot().resolve("day4-orchestration-evals-cicd/java/lab51");
        return Files.isDirectory(module) ? module.resolve("runs.sqlite").toString() : "runs.sqlite";
    }

    static Path lab5Dir() {
        return Spans.repoRoot().resolve("day4-orchestration-evals-cicd/lab5-1-handoff");
    }

    /** A fixture path as given, or relative to lab5-1-handoff/ (so `replay fixtures/...` works from anywhere). */
    static Path resolveFixture(String given) {
        Path p = Path.of(given);
        if (Files.exists(p)) return p;
        Path inLab = lab5Dir().resolve(given);
        return Files.exists(inLab) ? inLab : p;
    }

    // --------------------------------------------------------------- tokens
    void issueTokens(String account) {
        Path ops = Spans.repoRoot().resolve("day3-integration-security/aira-ops/aira_ops.py").toAbsolutePath();
        Path callers = ops.getParent().resolve("callers.json");
        String python = !env("PYTHON").isEmpty() ? env("PYTHON")
                : System.getProperty("os.name", "").toLowerCase().contains("win") ? "python" : "python3";
        String rd = issue(python, ops, callers, "pipeline-agents", "--accounts", account);
        String wr = issue(python, ops, callers, "pipeline-apply", "--write");
        out.println("# Tokens are shown once; callers.json keeps only their hashes. Restart aira-ops with --callers.");
        out.println("export AIRA_OPS_READ_TOKEN=" + rd + "\nexport AIRA_OPS_APPLY_TOKEN=" + wr);
        if (System.getProperty("os.name", "").toLowerCase().contains("win")) {
            out.println("# PowerShell:\n$env:AIRA_OPS_READ_TOKEN=\"" + rd + "\"\n$env:AIRA_OPS_APPLY_TOKEN=\"" + wr + "\"");
        }
        // Absolute paths: pasted from any folder, aira-ops must load THIS callers.json, or every agent call is refused.
        out.println("# then restart aira-ops (same AIRA_OPS_TOKEN as before):\n#   " + python + " \"" + ops + "\" --callers \"" + callers + "\"");
    }

    private String issue(String python, Path ops, Path callers, String actor, String... extra) {
        List<String> cmd = new ArrayList<>(List.of(python, ops.toString(), "--callers", callers.toString(), "--issue-token", actor));
        cmd.addAll(List.of(extra));
        ProcessBuilder pb = new ProcessBuilder(cmd);
        if (env("AIRA_OPS_TOKEN").isEmpty()) pb.environment().put("AIRA_OPS_TOKEN", "x");
        try {
            Process proc = pb.start();
            String stdout = read(proc.getInputStream());
            String stderr = read(proc.getErrorStream());
            int code = proc.waitFor();
            if (code != 0) {
                throw new Exit(1, "aira_ops.py --issue-token " + actor + " failed (exit " + code + "): " + stderr.strip());
            }
            return stdout.strip();
        } catch (IOException e) {
            throw new Exit(1, "cannot run " + python + " (set PYTHON to your Python 3 interpreter): " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new Exit(1, "interrupted");
        }
    }

    private static String read(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        in.transferTo(buf);
        return buf.toString(StandardCharsets.UTF_8);
    }
}
