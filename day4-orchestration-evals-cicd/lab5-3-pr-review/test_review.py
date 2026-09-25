"""Lab 5.3 tests - every control around the model, no model.

A stub `claude` on PATH plays the headless run: it records what it was sent
(argv, stdin, env) and prints a canned result. Run: python3 -m unittest test_review -v
"""
import json, os, stat, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "starter" if os.environ.get("LAB53_TARGET") == "starter" else HERE
sys.path.insert(0, str(TARGET))
import review  # noqa: E402

DIFF = textwrap.dedent('''\
    diff --git a/svc/apply.py b/svc/apply.py
    index 1111111..2222222 100644
    --- a/svc/apply.py
    +++ b/svc/apply.py
    @@ -10,6 +10,8 @@ def apply(store, rid, token):
         a = store.approval(rid)
    -    if not a or a["decision"] != "approve":
    -        raise GateError("no approval on record")
    +    if store.run(rid)["status"] == "approved":
    +        pass
         op_id = store.operation(rid) or new_op_id()
    +    API_TOKEN = "live-7f3a9c2e5b1d4a6f8e0c"
         return send(op_id, token)
    diff --git a/README.md b/README.md
    --- a/README.md
    +++ b/README.md
    @@ -1,2 +1,3 @@
     # Labs
    +Run the pipeline with `python3 pipeline.py run`.
    ''')

DIFF_NO_SECRET = "".join(l for l in DIFF.splitlines(True) if "API_TOKEN" not in l)

STUB = r'''#!/usr/bin/env python3
import json, os, sys
d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the test's temp dir
open(os.path.join(d, "argv.json"), "w").write(json.dumps(sys.argv[1:]))
open(os.path.join(d, "stdin.txt"), "w").write(sys.stdin.read())
open(os.path.join(d, "env.json"), "w").write(json.dumps(dict(os.environ)))
seen = {}
for root, _, files in os.walk(os.getcwd()):
    for f in files:
        fp = os.path.join(root, f)
        try: seen[os.path.relpath(fp)] = open(fp, encoding="utf-8").read()
        except Exception: seen[os.path.relpath(fp)] = None
open(os.path.join(d, "workspace.json"), "w").write(json.dumps({"cwd": os.getcwd(), "files": seen}))
r = json.load(open(os.path.join(d, "reply.json")))
print(json.dumps(r)); sys.exit(r.pop("_exit", 0))
'''

def reply(findings, **kw):
    return dict({"type": "result", "is_error": False, "subtype": "success", "num_turns": 3, "total_cost_usd": 0.09,
                 "structured_output": {"summary": "Removes the approval check.", "findings": findings}}, **kw)

GOOD_FINDING = {"severity": "blocker", "file": "svc/apply.py", "line": 11, "title": "Approval check replaced by status check",
                "evidence": 'if store.run(rid)["status"] == "approved":', "why": "status field is not the decision record"}

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        if os.name == "nt":   # Windows runs claude through a .cmd shim, like npm's
            (self.tmp / "bin" / "claude.py").write_text(STUB)
            (self.tmp / "bin" / "claude.cmd").write_text(f'@"{sys.executable}" "%~dp0claude.py" %*\r\n')
        else:
            stub = self.tmp / "bin" / "claude"
            stub.write_text(STUB); stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        (self.tmp / "change.patch").write_text(DIFF)
        self.env = dict(os.environ, PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        ANTHROPIC_BASE_URL="https://gateway.example", ANTHROPIC_AUTH_TOKEN="gw-key-000000000000",
                        GITHUB_TOKEN="ghs_" + "x" * 36, AIRA_OPS_TOKEN="ops-admin-123456789")

    def run_review(self, findings=None, **kw):
        (self.tmp / "reply.json").write_text(json.dumps(reply(findings or [], **kw)))
        p = subprocess.run([sys.executable, str(TARGET / "review.py"), "--diff", str(self.tmp / "change.patch"),
                            "--repo", str(self.tmp), "--out", str(self.tmp / "out")],
                           env=self.env, capture_output=True, text=True, timeout=60)
        rj = json.loads((self.tmp / "out" / "review.json").read_text())
        return p.returncode, rj

    # --- reading the diff
    def test_changed_lines_are_numbered_on_the_new_side(self):
        ch = review.changed_lines(DIFF)
        self.assertEqual(ch["svc/apply.py"][11], '    if store.run(rid)["status"] == "approved":')
        self.assertIn(14, ch["svc/apply.py"])
        self.assertEqual(list(ch["README.md"]), [2])

    # --- secrets: found without the model, never sent to it
    def test_a_committed_secret_is_a_blocker_and_is_not_sent_to_the_model(self):
        code, rj = self.run_review([])
        self.assertEqual(code, 2)
        self.assertEqual([(f["source"], f["line"]) for f in rj["findings"]], [("pattern", 14)])
        sent = (self.tmp / "stdin.txt").read_text()
        self.assertNotIn("live-7f3a9c2e5b1d4a6f8e0c", sent)
        self.assertNotIn("live-7f3a9c2e5b1d4a6f8e0c", (self.tmp / "out" / "review.md").read_text())
        self.assertIn('API_TOKEN = "[REDACTED]"', sent)           # the name stays, so the model sees what it was

    def test_the_model_gets_the_gateway_key_and_nothing_else_secret(self):
        self.run_review([])
        env = json.loads((self.tmp / "env.json").read_text())
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "gw-key-000000000000")
        for leaked in ("GITHUB_TOKEN", "AIRA_OPS_TOKEN"):
            self.assertNotIn(leaked, env)          # allowlisted env: the reviewer can't leak what it never had

    # --- the headless invocation
    def test_headless_run_is_read_only_and_bounded(self):
        self.run_review([])
        argv = json.loads((self.tmp / "argv.json").read_text())
        opt = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--")}
        self.assertIn("-p", argv)
        self.assertEqual(opt["--tools"], "")                        # no tools: it sees only what we send
        self.assertEqual(opt["--permission-mode"], "dontAsk")
        self.assertEqual(opt["--setting-sources"], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--max-budget-usd", opt); self.assertIn("--json-schema", opt)

    # --- verification: the model's findings are claims
    def test_a_verified_blocker_blocks(self):
        code, rj = self.run_review([GOOD_FINDING])
        self.assertEqual(code, 2)
        self.assertIn("model", [f["source"] for f in rj["findings"]])

    def test_findings_that_do_not_point_at_a_changed_line_are_dropped(self):
        ghost = [dict(GOOD_FINDING, file="svc/other.py"),
                 dict(GOOD_FINDING, line=40),
                 dict(GOOD_FINDING, evidence="os.system(user_input)")]
        (self.tmp / "change.patch").write_text(DIFF_NO_SECRET)
        code, rj = self.run_review(ghost)
        self.assertEqual((code, rj["findings"], len(rj["dropped"])), (0, [], 3))

    def test_a_pr_that_only_deletes_a_check_can_still_be_blocked(self):
        # No added lines at all - the most dangerous kind of PR must not be unreviewable.
        (self.tmp / "change.patch").write_text(textwrap.dedent('''\
            diff --git a/svc/apply.py b/svc/apply.py
            --- a/svc/apply.py
            +++ b/svc/apply.py
            @@ -10,5 +10,3 @@ def apply(store, rid, token):
                 a = store.approval(rid)
            -    if not a or a["decision"] != "approve":
            -        raise GateError("no approval on record")
                 op_id = store.operation(rid) or new_op_id()
            '''))
        code, rj = self.run_review([dict(GOOD_FINDING, line=11, title="Approval check deleted",
                                         evidence='-    if not a or a["decision"] != "approve":')])
        self.assertEqual(code, 2, rj)
        self.assertEqual(rj["findings"][0]["title"], "Approval check deleted")

    def test_a_finding_with_no_evidence_is_dropped(self):
        (self.tmp / "change.patch").write_text(DIFF_NO_SECRET)
        code, rj = self.run_review([dict(GOOD_FINDING, evidence="   ")])
        self.assertEqual(code, 0)
        self.assertEqual(rj["dropped"][0]["dropped"], "no evidence quoted")

    def test_an_unquoted_token_assignment_is_a_blocker_and_is_masked(self):
        # the course's own token format, exactly as a shell line would carry it
        hexed = "910665cfcda086dfefb519136f1fe5ed"
        (self.tmp / "change.patch").write_text(textwrap.dedent(f'''\
            diff --git a/run.sh b/run.sh
            --- a/run.sh
            +++ b/run.sh
            @@ -1,1 +1,2 @@
             #!/bin/sh
            +export AIRA_OPS_TOKEN={hexed}
            '''))
        code, rj = self.run_review([])
        self.assertEqual(code, 2)
        self.assertEqual(rj["findings"][0]["source"], "pattern")
        self.assertNotIn(hexed, (self.tmp / "stdin.txt").read_text())
        for ok in ("AIRA_OPS_TOKEN=${AIRA_OPS_TOKEN}", "token = secrets.token_hex(16)", "max_tokens=4000",
                   "ANTHROPIC_AUTH_TOKEN=sk-PASTE-YOUR-KEY-HERE"):
            self.assertEqual(review.secret_findings({"x": {1: ok}}), [], ok)

    def test_minor_findings_do_not_block(self):
        (self.tmp / "change.patch").write_text(DIFF_NO_SECRET)
        code, _ = self.run_review([dict(GOOD_FINDING, severity="minor")])
        self.assertEqual(code, 0)

    # --- failing closed
    def test_a_model_error_is_exit_1_even_if_the_process_exits_0(self):
        code, rj = self.run_review([], is_error=True, result="API Error: 529 overloaded")
        self.assertEqual(code, 1); self.assertIn("is_error=True", rj["error"])

    def test_a_diff_over_the_cap_is_not_reviewed(self):
        self.env["REVIEW_MAX_DIFF_BYTES"] = "200"
        code, rj = self.run_review([])
        self.assertEqual(code, 1); self.assertIn("too large", rj["error"])
        self.assertFalse((self.tmp / "stdin.txt").exists(), "the model was called anyway")

    def test_the_model_sees_only_a_sanitised_copy_and_has_no_tools(self):
        repo = self.tmp / "repo"; repo.mkdir()
        run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True,
                                        env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                                 GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
        run("init", "-q", "-b", "main"); (repo / "app.py").write_text("x = 1\n"); run("add", "-A"); run("commit", "-qm", "base")
        run("switch", "-qc", "feat")
        (repo / "workshop_env.py").write_text('AIRA_OPS_APPLY_TOKEN = "apply-3f9c2a7e61b84d05a9e27c"\n')
        fake_key = "sk-" + "live-should-never-be-read-0000"     # built at runtime: the repo's commit hook scans for literals
        (repo / ".env").write_text(f"ANTHROPIC_AUTH_TOKEN={fake_key}\n")
        run("add", "-f", "-A"); run("commit", "-qm", "feat")
        (self.tmp / "reply.json").write_text(json.dumps(reply([])))
        p = subprocess.run([sys.executable, str(TARGET / "review.py"), "--repo", str(repo), "--base", "main",
                            "--head", "feat", "--out", str(self.tmp / "out")], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stderr)                     # the committed token is a blocker
        ws = json.loads((self.tmp / "workspace.json").read_text())
        self.assertNotEqual(os.path.realpath(ws["cwd"]), os.path.realpath(repo), "the model ran in the raw checkout")
        self.assertEqual(ws["files"], {}, "the model's working directory is not empty")
        sent = (self.tmp / "stdin.txt").read_text()
        self.assertIn('<file path="workshop_env.py">', sent)                 # context comes from the sanitised copy
        self.assertIn('AIRA_OPS_APPLY_TOKEN = "[REDACTED]"', sent)
        for secret in ("apply-3f9c2a7e61b84d05a9e27c", fake_key):
            self.assertNotIn(secret, sent)
        self.assertFalse(os.path.exists(ws["cwd"]), "the temporary directory was not cleaned up")

    def test_starter_differs_from_the_reference_only_inside_the_todo_blocks(self):
        import re
        def strip(src):
            src = re.sub(r"( *)# >>> TODO (\d).*?\1# <<< TODO \2\n", "", src, flags=re.S)
            return re.sub(r"HERE = Path\(__file__\).*?\n", "", src, count=1)
        ref, st = (HERE / "review.py").read_text(), (HERE / "starter" / "review.py").read_text()
        self.assertEqual(strip(st), strip(ref))
        self.assertEqual(st.count("raise NotImplementedError"), 3)

if __name__ == "__main__":
    unittest.main()
