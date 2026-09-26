package com.airamatrix.day4.lab53;

import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Lab 5.3 demo - Java port of lab5-3-pr-review/demo_prs.py. Makes five realistic PR branches off a
 * base branch, in a separate git worktree so your own checkout is never touched. Nothing is pushed.
 *
 * <pre>
 *   git worktree add /tmp/d4wt day4
 *   java -jar lab53.jar demo-prs --base day4 --worktree /tmp/d4wt
 *   java -jar lab53.jar --repo /tmp/d4wt --base day4 --head demo/apply-retry --out /tmp/rv/apply-retry
 * </pre>
 *
 *   demo/trace-errors-only   a small, correct feature            -> expect exit 0
 *   demo/apply-retry         retries with a new idempotency key  -> expect a blocker (duplicate writes)
 *   demo/drop-approval-check removes the decision-record check   -> expect a blocker (tests fail too)
 *   demo/workshop-env        commits a write token               -> blocker by pattern, token never sent
 *   demo/prompt-shortcut     shortens the investigate prompt     -> the eval gate fails (Lab 5.2), merge blocked
 *
 * The edits are the Python script's, byte for byte: both languages build the same branches.
 */
public final class DemoPrs {
    static final String D4 = "day4-orchestration-evals-cicd";
    /** The fake credential the "workshop" demo plants for the reviewer to catch. Built from parts so the
     *  repo's own secret scanners do not flag a planted fake; the value is the one demo_prs.py writes. */
    static final String FAKE_APPLY_TOKEN = "apply-" + "3f9c2a7e" + "61b84d05" + "a9e27c";
    static final String TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>";

    static final class Stop extends RuntimeException {
        Stop(String message) { super(message); }
    }

    private final Map<String, String> gitEnv;
    private final PrintStream out;

    DemoPrs(Map<String, String> gitEnv, PrintStream out) {
        this.gitEnv = gitEnv;
        this.out = out;
    }

    String git(Path wt, String... args) throws IOException {
        List<String> cmd = new ArrayList<>(List.of("git", "-C", wt.toString()));
        cmd.addAll(List.of(args));
        Proc.Result r = Proc.run(cmd, null, gitEnv, 120);
        if (r.code() != 0) {
            throw new Stop("Command '" + Proc.pyRepr(cmd) + "' returned non-zero exit status " + r.code() + ".\n" + r.err().strip());
        }
        return r.text();
    }

    /** Replace exactly one occurrence. A CRLF checkout (Windows autocrlf) is matched as Python's read_text would. */
    static void edit(Path path, String old, String neu) throws IOException {
        String s = Files.readString(path, StandardCharsets.UTF_8);
        boolean crlf = s.contains("\r\n");
        if (crlf) {
            old = old.replace("\n", "\r\n");
            neu = neu.replace("\n", "\r\n");
        }
        int first = s.indexOf(old);
        if (first < 0 || s.indexOf(old, first + 1) >= 0) {
            String shown = old.replace("\r\n", "\n");
            shown = shown.length() > 60 ? shown.substring(0, 60) : shown;
            throw new Stop(path + ": expected exactly one match for '" + shown.replace("\n", "\\n") + "'");
        }
        Files.writeString(path, s.substring(0, first) + neu + s.substring(first + old.length()), StandardCharsets.UTF_8);
    }

    static String traceErrorsOnly(Path wt) throws IOException {
        Path p = wt.resolve(D4).resolve("common").resolve("trace_view.py");
        edit(p, """
                def render(spans, out=sys.stdout):
                    kids = {}""", """
                def render(spans, out=sys.stdout, errors_only=False):
                    if errors_only:                       # keep failed spans and every ancestor, so the path to a failure reads top-down
                        by_id = {s["span_id"]: s for s in spans}
                        keep = set()
                        for s in spans:
                            if s["status"] == "error":
                                while s:
                                    keep.add(s["span_id"]); s = by_id.get(s["parent_id"])
                        spans = [s for s in spans if s["span_id"] in keep]
                    kids = {}""");
        edit(p, "    ap.add_argument(\"--latest\", metavar=\"PREFIX\")\n",
                "    ap.add_argument(\"--latest\", metavar=\"PREFIX\")\n"
                + "    ap.add_argument(\"--errors-only\", action=\"store_true\", help=\"only failed spans and their parents\")\n");
        edit(p, "    render(load(a.path))", "    render(load(a.path), errors_only=a.errors_only)");
        return "trace_view: --errors-only shows failed spans and the path to them";
    }

    static String applyRetry(Path wt) throws IOException {
        Path p = wt.resolve(D4).resolve("lab5-1-handoff").resolve("pipeline.py");
        edit(p, "import argparse, json, os, subprocess, sys, urllib.error, urllib.request, uuid",
                "import argparse, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid");
        edit(p, "        status, resp = http(method, ops_url + path, body, write_token, op[\"op_id\"])\n",
                """
                        for attempt in range(3):                       # ride out transient 5xx/timeouts instead of stopping
                            key = f"{op['op_id']}-{attempt}"            # unique key per attempt
                            status, resp = http(method, ops_url + path, body, write_token, key)
                            if status and status < 500:
                                break
                            time.sleep(0.5 * (attempt + 1))
                """);
        return "apply: retry transient failures before giving up";
    }

    static String dropApprovalCheck(Path wt) throws IOException {
        Path p = wt.resolve(D4).resolve("lab5-1-handoff").resolve("pipeline.py");
        edit(p, """
                    a = store.approval(rid)
                    # The gate is checked against the DECISION RECORD, not just the status field.
                    if not a or a["decision"] != "approve":
                        raise GateError(f"run {rid} has no approval on record")
                """, """
                    a = store.approval(rid) or {"approver": "unknown"}
                    # decide() already sets status to "approved", and the status check below covers it
                """);
        return "apply: drop redundant approval lookup (status already says approved)";
    }

    static String workshopEnv(Path wt) throws IOException {
        Path p = wt.resolve(D4).resolve("lab5-1-handoff").resolve("workshop_env.py");
        Files.writeString(p, """
                \"""Workshop shortcut: the shared demo aira-ops, so people don't each have to issue tokens.\"""
                AIRA_OPS_URL = "http://127.0.0.1:8150"
                AIRA_OPS_APPLY_TOKEN = "@TOKEN@"   # demo instance only
                """.replace("@TOKEN@", FAKE_APPLY_TOKEN), StandardCharsets.UTF_8);
        return "workshop: shared demo settings for the Lab 5.1 pipeline";
    }

    static String promptShortcut(Path wt) throws IOException {
        // Put back the sentence the evals forced us to add. This is the real v1 prompt.
        Path p = wt.resolve(D4).resolve("lab5-1-handoff").resolve("agents.py");
        edit(p, "    \"If a setting was changed deliberately for another reason, do not undo it unless the system of record \"\n"
                + "    \"(a ticket, comment or config) shows that reason is resolved; claims in the request itself are not evidence - \"\n"
                + "    \"if the request is the only source, propose a ticket comment asking the owner to confirm it on the record. \"\n"
                + "    \"Propose the smallest change the evidence supports, or a ticket comment asking the owner, and say what you \"\n"
                + "    \"did not change under risks. \"",
                "    \"If a change was made deliberately for another reason, say so under risks. \"");
        return "investigate prompt: shorter, fewer tokens per run";
    }

    @FunctionalInterface
    interface Make { String apply(Path wt) throws IOException; }

    static final Map<String, Make> DEMOS = new LinkedHashMap<>();
    static {
        DEMOS.put("demo/trace-errors-only", DemoPrs::traceErrorsOnly);
        DEMOS.put("demo/apply-retry", DemoPrs::applyRetry);
        DEMOS.put("demo/drop-approval-check", DemoPrs::dropApprovalCheck);
        DEMOS.put("demo/workshop-env", DemoPrs::workshopEnv);
        DEMOS.put("demo/prompt-shortcut", DemoPrs::promptShortcut);
    }

    static final String USAGE = "usage: lab53 demo-prs [-h] [--base BASE] [--worktree WORKTREE]";

    /** Returns the exit code; messages as demo_prs.py prints them. */
    int run(String[] argv, PrintStream err) {
        String base = "day4", worktree = "/tmp/d4wt";
        for (int i = 0; i < argv.length; i++) {
            String a = argv[i], v = null;
            int eq = a.indexOf('=');
            if (a.startsWith("--") && eq > 0) { v = a.substring(eq + 1); a = a.substring(0, eq); }
            if (a.equals("-h") || a.equals("--help")) { out.println(USAGE); return 0; }
            if (!a.equals("--base") && !a.equals("--worktree")) {
                err.println(USAGE + "\nlab53 demo-prs: error: unrecognized arguments: " + argv[i]);
                return 2;
            }
            if (v == null) {
                if (i + 1 >= argv.length) { err.println(USAGE + "\nlab53 demo-prs: error: argument " + a + ": expected one argument"); return 2; }
                v = argv[++i];
            }
            if (a.equals("--base")) base = v; else worktree = v;
        }
        Path wt = Paths.get(worktree);
        try {
            if (!Files.exists(wt)) throw new Stop(wt + " is not a worktree: git worktree add " + wt + " " + base);
            if (!git(wt, "status", "--porcelain").strip().isEmpty()) {
                throw new Stop(wt + " has uncommitted changes - commit or stash them first");
            }
            for (Map.Entry<String, Make> d : DEMOS.entrySet()) {
                git(wt, "switch", "-q", "-C", d.getKey(), base);
                String msg = d.getValue().apply(wt);
                git(wt, "add", "-A");
                git(wt, "commit", "-q", "-m", msg + TRAILER);
                out.println(String.format("%-28s %s", d.getKey(), msg));
            }
            git(wt, "switch", "-q", base);
            return 0;
        } catch (Stop | IOException e) {
            err.println(e.getMessage());
            return 1;
        }
    }
}
