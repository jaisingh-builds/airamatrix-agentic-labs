"""Live tests - they call the model and cost money, so they run only when asked:

    LAB_LIVE=1 python3 -m unittest tests.test_live -v                      # ~$0.03: one run through the gateway
    LAB_LIVE=1 LAB_LIVE_AWS=1 python3 -m unittest tests.test_live -v       # + one invoke of YOUR deployed runtime

Never set AIRA_OPS_TOKEN / AIRA_OPS_APPLY_TOKEN in the shell that runs these: the agent refuses to start.
"""
import os, shutil, sys, tempfile, unittest

from responder import repo, responder
from responder.agent import ResponderAgent
from responder.ops import HttpOpsReader, PrivateOps
from responder.util import parse_instant

LIVE = os.environ.get("LAB_LIVE") == "1"


@unittest.skipUnless(LIVE, "live: set LAB_LIVE=1 (calls the model through the gateway, costs money)")
class LiveLocalTest(unittest.TestCase):
    def test_one_real_run_reaches_the_human_gate_with_the_guardrail_passing(self):
        from agentic_core import Config, GatewayClient
        cfg = Config().require()
        tmp = tempfile.mkdtemp(prefix="capstone-live-")
        try:
            with PrivateOps(["ACC-1001"]) as ops:
                tr = repo.spans.Tracer("capstone", trace_id="live-test", root=tmp)
                agent = ResponderAgent(GatewayClient(cfg).messages, cfg.model, 10, 0.30)
                o = responder.run("live", "ACC-1001", parse_instant("2026-09-24T10:30:00+05:30"), None,
                                  HttpOpsReader(ops.url, ops.read_tokens["ACC-1001"]), agent, tr)
            self.assertIn(o.status, ("awaiting_approval", "no_action"), o.error or o.verdict)
            self.assertTrue(o.verdict.passed, o.verdict.denials)
            self.assertEqual("sla_report", o.tool_calls[0].name, "sla_report first")
            self.assertLess(o.cost_usd, 0.30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(LIVE and os.environ.get("LAB_LIVE_AWS") == "1", "live AWS: set LAB_LIVE=1 LAB_LIVE_AWS=1 after deploy")
class LiveAgentCoreTest(unittest.TestCase):
    def test_the_deployed_runtime_answers_with_a_run_record_and_its_trace(self):
        sys.path.insert(0, str(repo.python_dir() / "agentcore"))
        import capstone_aws
        r = capstone_aws.call("ACC-1005", "2026-09-24T10:30:00+05:30", "Anything due for Riverbend Research Institute?",
                              "capstone-live-test", "0123456789")
        self.assertEqual("agentcore", r["mode"])
        self.assertIn(r["status"], ("no_action", "awaiting_approval", "blocked"), r.get("error"))
        self.assertTrue(any(s["name"] == "run" for s in r["trace"]))


if __name__ == "__main__":
    unittest.main()
