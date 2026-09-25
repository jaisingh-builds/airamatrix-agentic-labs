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
        self.assertEqual(opt["--tools"], "Read,Grep,Glob")
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
