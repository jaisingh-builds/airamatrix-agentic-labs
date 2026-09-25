"""Lab 5.1 tests - every control, no model. A fake runner plays the two agents;
aira-ops is real, with per-caller tokens.

    python3 -m unittest test_pipeline -v
    LAB51_TARGET=starter python3 -m unittest test_pipeline    # run against YOUR code
"""
import json, os, secrets, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE.parents[1] / "day3-integration-security" / "aira-ops" / "aira_ops.py"
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "common"))
if os.environ.get("LAB51_TARGET") == "starter":
    sys.path.insert(0, str(HERE / "starter"))
import pipeline  # noqa: E402
import agents  # noqa: E402
from agents import AgentResult  # noqa: E402
from store import Store  # noqa: E402

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

GOOD = {"diagnosis": "ingest.max_concurrent_jobs was cut from 16 to 4, capping throughput below demand.",
        "evidence": ["T-1001 on-call comment suspects the concurrency cap",
                     "config ingest.max_concurrent_jobs = 4 (version 1)"],
        "confidence": "high", "risks": ["Lowered on purpose during a memory investigation"],
        "proposed_change": {"action": "update_config", "key": "ingest.max_concurrent_jobs",
                            "value": 8, "expected_version": 1}}
APPROVE = {"verdict": "approve", "checks": [{"claim": "value is 4", "verified": True, "source": "get_config"}],
           "reasons": ["claims verified; halfway step is proportionate"]}
BLOCK = {"verdict": "block", "checks": [{"claim": "value is 4", "verified": True}],
         "reasons": ["overrides a deliberate change with no evidence the memory issue is fixed"]}

class FakeRunner:
    """Plays the agents from a script: stage -> list of outputs or exceptions."""
    def __init__(self, **script):
        self.script, self.calls = {k: list(v) for k, v in script.items()}, []
    def run(self, stage, system, prompt, schema):
        self.calls.append(stage)
        nxt = self.script[stage].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return AgentResult(json.loads(json.dumps(nxt)), 0.03, [("get_config", {"key": "ingest.max_concurrent_jobs"})], 4)

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        callers = Path(cls.tmp.name) / "callers.json"
        env = dict(os.environ, AIRA_OPS_TOKEN="admin-" + secrets.token_hex(8))
        cls.admin = env["AIRA_OPS_TOKEN"]
        def issue(*a):
            return subprocess.run([sys.executable, str(OPS), "--callers", str(callers), "--issue-token", *a],
                                  capture_output=True, text=True, env=env, check=True).stdout.strip()
        cls.read_tok = issue("pipeline-agents", "--accounts", "ACC-1001")
        cls.write_tok = issue("pipeline-apply", "--write")
        cls.port = free_port()
        cls.ops = subprocess.Popen([sys.executable, str(OPS), "--port", str(cls.port), "--quiet", "--latency", "0",
                                    "--db", str(Path(cls.tmp.name) / "o.sqlite"), "--callers", str(callers)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.url = f"http://127.0.0.1:{cls.port}"
        for _ in range(50):
            try: urllib.request.urlopen(cls.url + "/health", timeout=0.2); break
            except Exception: time.sleep(0.1)
        os.environ["LAB_TRACE_DIR"] = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.ops.terminate(); cls.ops.wait(); cls.tmp.cleanup()

    def setUp(self):
        self.store = Store(str(Path(self.tmp.name) / f"runs-{secrets.token_hex(4)}.sqlite"))
        pipeline.APPLY_TIMEOUT_S = 5

    def get(self, path, token=None):
        req = urllib.request.Request(self.url + path, headers={"Authorization": f"Bearer {token or self.admin}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def new_run(self, runner):
        rid = self.store.create_run("ACC-1001", "Ingest backlog on T-1001")
        pipeline.advance(self.store, runner, rid)
        return rid

    # --- the gate
    def test_pipeline_stops_at_the_gate_and_nothing_is_written(self):
        rid = self.new_run(FakeRunner(investigate=[GOOD], review=[APPROVE]))
        self.assertEqual(self.store.run(rid)["status"], "awaiting_approval")
        with self.assertRaises(pipeline.GateError):
            pipeline.apply(self.store, rid, self.write_tok, self.url)
        self.assertEqual(self.get("/config/ingest.max_concurrent_jobs")["version"], 1)

    def test_approved_change_is_applied_once_by_the_apply_identity(self):
        cur = self.get("/config/alerts.ingest_latency_minutes")
        change = dict(GOOD, proposed_change={"action": "update_config", "key": "alerts.ingest_latency_minutes",
                                             "value": 20, "expected_version": cur["version"]})
        rid = self.new_run(FakeRunner(investigate=[change], review=[APPROVE]))
        pipeline.decide(self.store, rid, "approve", "Jai", "latency alert is too noisy")
        self.assertEqual(pipeline.apply(self.store, rid, self.write_tok, self.url)["status"], "applied")
        self.assertEqual(self.get("/config/alerts.ingest_latency_minutes")["value"], 20)
        top = self.get("/audit")["entries"][0]
        self.assertEqual((top["actor"], top["verified"]), ("pipeline-apply", 1))
        pipeline.apply(self.store, rid, self.write_tok, self.url)          # again: nothing more happens
        self.assertEqual(self.get("/config/alerts.ingest_latency_minutes")["version"], cur["version"] + 1)

    def test_the_gate_trusts_the_decision_record_not_the_status_field(self):
        rid = self.new_run(FakeRunner(investigate=[GOOD], review=[APPROVE]))
        self.store.set_status(rid, "approved")                              # someone edits the status by hand
        with self.assertRaises(pipeline.GateError):
            pipeline.apply(self.store, rid, self.write_tok, self.url)

    def test_decision_needs_a_name_and_a_reason_and_happens_once(self):
        rid = self.new_run(FakeRunner(investigate=[GOOD], review=[APPROVE]))
        for who, why in (("", "ok"), ("Jai", " ")):
            with self.assertRaises(pipeline.GateError):
                pipeline.decide(self.store, rid, "approve", who, why)
        pipeline.decide(self.store, rid, "reject", "Jai", "wait for the memory fix")
        with self.assertRaises(pipeline.GateError):
            pipeline.decide(self.store, rid, "approve", "Someone", "changed my mind")
        with self.assertRaises(pipeline.GateError):
            pipeline.apply(self.store, rid, self.write_tok, self.url)

    def test_a_reviewer_block_needs_an_explicit_override(self):
        rid = self.new_run(FakeRunner(investigate=[GOOD], review=[BLOCK]))
        self.assertEqual(self.store.run(rid)["status"], "needs_rework")
        with self.assertRaises(pipeline.GateError):
            pipeline.decide(self.store, rid, "approve", "Jai", "looks fine")
        pipeline.decide(self.store, rid, "approve", "Jai", "memory fix shipped in 2.4.1", override=True)
        self.assertEqual(self.store.approval(rid)["override"], 1)

    # --- contracts between stages
    def test_a_proposal_outside_the_contract_never_reaches_the_gate(self):
        bad = dict(GOOD, proposed_change={"action": "delete_account", "key": "ingest.max_concurrent_jobs"})
        with self.assertRaises(Exception):
            self.new_run(FakeRunner(investigate=[bad]))
        runs = self.store.runs()
        self.assertEqual(runs[0]["status"], "investigate_failed")
        self.assertIsNone(self.store.stage(runs[0]["id"], "review"))

    def test_a_key_outside_the_allowlist_is_refused_even_when_complete(self):
        bad = dict(GOOD, proposed_change={"action": "update_config", "key": "feature.ai_triage_enabled",
                                          "value": False, "expected_version": 1})
        with self.assertRaises(Exception):
            self.new_run(FakeRunner(investigate=[bad]))
        self.assertEqual(self.store.runs()[0]["status"], "investigate_failed")

    def test_a_malformed_verdict_is_a_failed_review(self):
        with self.assertRaises(Exception):
            self.new_run(FakeRunner(investigate=[GOOD], review=[dict(APPROVE, verdict="ship it")]))
        self.assertEqual(self.store.runs()[0]["status"], "review_failed")

    def test_an_incomplete_change_is_a_failed_stage_not_a_guess(self):
        bad = dict(GOOD, proposed_change={"action": "update_config", "key": "ingest.max_concurrent_jobs", "value": 8})
        with self.assertRaises(Exception):
            self.new_run(FakeRunner(investigate=[bad]))

    def test_nothing_to_change_ends_the_run_without_a_review(self):
        runner = FakeRunner(investigate=[dict(GOOD, proposed_change={"action": "none"})])
        rid = self.new_run(runner)
        self.assertEqual((self.store.run(rid)["status"], runner.calls), ("no_change", ["investigate"]))

    # --- checkpointing
    def test_resume_skips_finished_stages(self):
        runner = FakeRunner(investigate=[GOOD], review=[agents.RunnerError("review: structured output failed", 0.11), APPROVE])
        with self.assertRaises(agents.RunnerError):
            self.new_run(runner)
        rid = self.store.runs()[0]["id"]
        self.assertEqual(self.store.run(rid)["status"], "review_failed")
        pipeline.resume(self.store, runner, rid, self.write_tok)
        self.assertEqual(runner.calls, ["investigate", "review", "review"], "investigate was paid for twice")
        self.assertEqual(self.store.run(rid)["status"], "awaiting_approval")
        self.assertAlmostEqual(self.store.cost(rid), 0.03 + 0.11 + 0.03, places=4, msg= "the failed attempt was paid for - it must show in the cost")

    # --- the write: orchestrator-owned retry state
    def test_unknown_outcome_is_retried_with_the_same_operation_id(self):
        slow_port = free_port()
        slow = subprocess.Popen([sys.executable, str(OPS), "--port", str(slow_port), "--quiet", "--latency", "0.8",
                                 "--db", str(Path(self.tmp.name) / "slow.sqlite"), "--reset"],
                                env=dict(os.environ, AIRA_OPS_TOKEN=self.admin), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{slow_port}"
        try:
            for _ in range(50):
                try: urllib.request.urlopen(url + "/health", timeout=0.2); break
                except Exception: time.sleep(0.1)
            change = dict(GOOD, proposed_change={"action": "add_ticket_comment", "ticket_id": "T-1001",
                                                 "comment": "Concurrency raised to 8 after approval."})
            rid = self.new_run(FakeRunner(investigate=[change], review=[APPROVE]))
            pipeline.decide(self.store, rid, "approve", "Jai", "comment only")
            pipeline.APPLY_TIMEOUT_S = 0.3
            self.assertEqual(pipeline.apply(self.store, rid, self.admin, url)["status"], "outcome_unknown")
            op1 = self.store.operation(rid)["op_id"]
            time.sleep(1.0)                                               # the write landed anyway
            pipeline.APPLY_TIMEOUT_S = 5
            self.assertEqual(pipeline.apply(self.store, rid, self.admin, url)["status"], "applied")
            self.assertEqual(self.store.operation(rid)["op_id"], op1)
            req = urllib.request.Request(url + "/tickets/T-1001", headers={"Authorization": f"Bearer {self.admin}"})
            body = json.loads(urllib.request.urlopen(req).read())
            self.assertEqual([c["body"] for c in body["comments"]].count("Concurrency raised to 8 after approval."), 1)
        finally:
            slow.terminate(); slow.wait()

    # --- least privilege
    def test_the_agents_token_cannot_write(self):
        rid = self.new_run(FakeRunner(investigate=[GOOD], review=[APPROVE]))
        pipeline.decide(self.store, rid, "approve", "Jai", "test")
        self.assertEqual(pipeline.apply(self.store, rid, self.read_tok, self.url)["status"], "apply_failed")
        self.assertEqual(json.loads(self.store.db.execute("select response from operations").fetchone()[0])["error"]["code"], "forbidden")

    def test_starter_differs_from_the_reference_only_inside_the_todo_blocks(self):
        import re
        def strip(src):
            src = re.sub(r"( *)# >>> TODO (\d).*?\1# <<< TODO \2\n", "", src, flags=re.S)
            src = src.split("Lab 5.1 - a two-stage", 1)[1]
            return re.sub(r"HERE = Path\(__file__\).*?\n", "", src, count=1)
        ref, st = (HERE / "pipeline.py").read_text(), (HERE / "starter" / "pipeline.py").read_text()
        self.assertEqual(strip(st), strip(ref))
        self.assertEqual(st.count("raise NotImplementedError"), 4)

    def test_sdk_stage_options_are_least_privilege(self):
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            self.skipTest("claude-agent-sdk not installed (pip install claude-agent-sdk)")
        import agents
        o = agents.SdkRunner(self.url, "tok").options("investigate", "sys", {"type": "object"})
        self.assertEqual(o.tools, [])
        self.assertTrue(o.strict_mcp_config); self.assertEqual(o.setting_sources, [])
        self.assertEqual(o.permission_mode, "dontAsk")
        self.assertTrue(all(t.split("__")[-1] in agents.READ_TOOLS for t in o.allowed_tools))
        self.assertEqual(o.mcp_servers["aira-ops"]["env"]["AIRA_OPS_READONLY"], "1")

if __name__ == "__main__":
    unittest.main()
