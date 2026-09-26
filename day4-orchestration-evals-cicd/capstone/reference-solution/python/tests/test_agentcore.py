"""AgentCore mode without AWS: the Converse translation, the Gateway reader, the runtime's deploy inputs and the
record/replay of an AgentCore run - all offline. The Gateway is faked on top of a real private aira-ops."""
import io, json, os, secrets, shutil, sys, tempfile, unittest
from pathlib import Path

from responder import cli, gate, guardrails, repo, responder
from responder.agent import ModelError, ResponderAgent
from responder.agentcore_io import ConverseModel, GatewayOpsReader, from_converse, to_converse
from responder.gate import GateError
from responder.ops import HttpOpsReader, OpsError, PrivateOps
from responder.store import Store
from responder.tools import SUBMIT
from responder.util import parse_instant

sys.path.insert(0, str(repo.python_dir() / "agentcore"))
import capstone_aws  # noqa: E402

from tests.test_capstone import good  # noqa: E402

T1030 = parse_instant("2026-09-24T10:30:00+05:30")
OPS, TMP, _saved = None, None, None


def setUpModule():
    global OPS, TMP, _saved
    TMP = tempfile.mkdtemp(prefix="capstone-ac-test-")
    _saved = os.environ.get("LAB_TRACE_DIR")
    os.environ["LAB_TRACE_DIR"] = os.path.join(TMP, "traces")
    OPS = PrivateOps(["ACC-1001", "ACC-1003"])


def tearDownModule():
    OPS.close()
    if _saved is None:
        os.environ.pop("LAB_TRACE_DIR", None)
    else:
        os.environ["LAB_TRACE_DIR"] = _saved
    shutil.rmtree(TMP, ignore_errors=True)


class FakeGateway:
    """tools/call ops-read___X -> the private aira-ops, answered the way the AgentCore Gateway answers:
    the aira-ops body as text content, isError on an HTTP error, a JSON-RPC error for a tool Cedar denies.
    Its credential reads EVERY account - like the shared Gateway's - so the code boundary is what is tested."""

    def __init__(self):
        self.readers = {a: HttpOpsReader(OPS.url, t) for a, t in OPS.read_tokens.items()}
        self.calls = []

    def __call__(self, body):
        name, args = body["params"]["name"], body["params"]["arguments"]
        self.calls.append(name)
        if not name.startswith("ops-read___"):
            return {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32002, "message": "Tool call not permitted by policy"}}
        tool = name[len("ops-read___"):]
        try:
            data = self._read(tool, args)
            return {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": json.dumps(data)}]}}
        except OpsError as e:
            text = json.dumps({"error": {"code": e.code, "message": str(e)}})
            return {"jsonrpc": "2.0", "id": body["id"], "result": {"isError": True, "content": [{"type": "text", "text": text}]}}

    def _read(self, tool, args):
        if tool == "get_ticket":
            for r in self.readers.values():
                try:
                    return r.ticket(args["ticket_id"])
                except OpsError:
                    continue
            raise OpsError(404, "not_found", f"no ticket {args['ticket_id']}")
        acc = args.get("account_id", "ACC-1001")
        r = self.readers[acc]
        return {"lookup_account": lambda: r.account(acc), "search_tickets": lambda: r.tickets(acc),
                "list_jobs": lambda: r.jobs(acc), "get_config": lambda: r.config(args["key"])}[tool]()


def converse_reply(*blocks, stop="tool_use"):
    return {"output": {"message": {"role": "assistant", "content": list(blocks)}}, "stopReason": stop,
            "usage": {"inputTokens": 1000, "outputTokens": 100}}


def tool_block(name, input_):
    return {"toolUse": {"toolUseId": "tu_" + secrets.token_hex(3), "name": name, "input": input_}}


class ConverseTest(unittest.TestCase):
    def test_messages_translate_both_ways(self):
        msgs = [{"role": "user", "content": "Account: ACC-1001"},
                {"role": "assistant", "content": [{"type": "text", "text": " "},
                                                  {"type": "tool_use", "id": "t1", "name": "sla_report", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}", "is_error": True}]}]
        c = to_converse(msgs)
        self.assertEqual([{"text": "Account: ACC-1001"}], c[0]["content"])
        self.assertEqual([{"toolUse": {"toolUseId": "t1", "name": "sla_report", "input": {}}}], c[1]["content"], "blank text dropped")
        self.assertEqual("error", c[2]["content"][0]["toolResult"]["status"])
        r = from_converse(converse_reply({"text": "hi"}, tool_block("get_ticket", {"ticket_id": "T-1001"}),
                                         tool_block("submit_proposal", {"exposed": [{"elapsed_minutes": 275.0, "x": 0.5}]})))
        self.assertEqual({"exposed": [{"elapsed_minutes": 275, "x": 0.5}]}, r["content"][2]["input"], "275.0 -> 275")
        self.assertIs(int, type(r["content"][2]["input"]["exposed"][0]["elapsed_minutes"]))
        self.assertEqual(["text", "tool_use", "tool_use"], [b["type"] for b in r["content"]])
        self.assertEqual({"input_tokens": 1000, "output_tokens": 100}, r["usage"])

    def test_the_guardrail_is_on_every_call_and_errors_carry_a_status(self):
        seen = []

        def converse(**req):
            seen.append(req)
            return converse_reply({"text": "ok"}, stop="end_turn")
        m = ConverseModel(converse, "model-x", "gr-1", "3")
        m([{"role": "user", "content": "x"}], [{"name": "a", "description": "d", "input_schema": {"type": "object"}}], "sys", 3000)
        self.assertEqual({"guardrailIdentifier": "gr-1", "guardrailVersion": "3", "trace": "enabled"}, seen[0]["guardrailConfig"])
        self.assertEqual({"json": {"type": "object"}}, seen[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"])

        class Throttled(Exception):
            response = {"ResponseMetadata": {"HTTPStatusCode": 429}, "Error": {"Message": "slow down"}}

        def boom(**req):
            raise Throttled()
        with self.assertRaises(ModelError) as cm:
            ConverseModel(boom, "m", "g", "1")([{"role": "user", "content": "x"}], [], "s", 10)
        self.assertEqual(429, cm.exception.status)


class GatewayRunTest(unittest.TestCase):
    def test_a_full_run_through_the_gateway_and_converse_adapters(self):
        gw = FakeGateway()
        script = iter([converse_reply(tool_block("sla_report", {})),
                       converse_reply(tool_block("get_ticket", {"ticket_id": "T-1007"})),
                       converse_reply(tool_block(SUBMIT, good()))])
        model = ConverseModel(lambda **req: next(script), "m", "g", "1")
        tr = repo.spans.Tracer("capstone", trace_id="ac-" + secrets.token_hex(3))
        o = responder.run("ac1", "ACC-1001", T1030, None, GatewayOpsReader("https://gw.invalid/mcp", "tok", post=gw),
                          ResponderAgent(model, "claude-sonnet", 10, 1.0, {}), tr)
        self.assertEqual("awaiting_approval", o.status, o.verdict)
        # the Gateway's credential could read T-1007 (ACC-1003) - the code refused it anyway
        self.assertEqual(["sla_report", "get_ticket"], [c.name for c in o.tool_calls])
        self.assertFalse(o.tool_calls[1].ok, "another tenant's ticket reads as not found")
        self.assertTrue(all(c.startswith("ops-read___") for c in gw.calls), gw.calls)

    def test_a_cedar_denial_is_an_ops_error_not_a_crash(self):
        r = GatewayOpsReader("https://gw.invalid/mcp", "tok", post=lambda b: {"error": {"message": "not permitted"}})
        with self.assertRaises(OpsError) as cm:
            r.account("ACC-1001")
        self.assertEqual(403, cm.exception.status)
        self.assertIn("gateway refused ops-read___lookup_account", str(cm.exception))


class AwsToolTest(unittest.TestCase):
    def setUp(self):
        self.state = Path(TMP) / f"state-{secrets.token_hex(3)}.json"
        self.state.write_text(json.dumps({
            "gateway_url": "https://gw.invalid/mcp", "guardrail_id": "gr-1", "guardrail_version": "2",
            "agent_bucket": "bucket", "user_pool": "pool", "token_url": "https://auth.invalid/token",
            "clients": {"investigator": {"client_id": "cid", "scopes": ["aira-ops/read"]}},
            "providers": {"investigator": {"name": "inv-provider", "arn": "arn:provider", "secret_arn": "arn:secret"}}}))
        self._shared, capstone_aws.SHARED = capstone_aws.SHARED, self.state
        self._own, capstone_aws.OWN = capstone_aws.OWN, Path(TMP) / f"own-{secrets.token_hex(3)}.json"

    def tearDown(self):
        capstone_aws.SHARED, capstone_aws.OWN = self._shared, self._own

    def test_the_runtime_gets_the_investigators_read_identity_and_no_write_access(self):
        env = capstone_aws.environment()
        self.assertEqual("inv-provider", env["OAUTH_PROVIDER"])
        self.assertEqual("aira-ops/read", env["OAUTH_SCOPES"])
        self.assertEqual("/tmp/traces", env["LAB_TRACE_DIR"])
        doc = json.dumps(capstone_aws.policy("123456789012"))
        for forbidden in ("InvokeAgentRuntime", "CreateEvent", "ops-write", "s3:", "iam:"):
            self.assertNotIn(forbidden, doc)
        self.assertIn("arn:provider", doc)
        self.assertEqual("aira_d4cap_py_responder", capstone_aws.RUNTIME_NAME)
        self.assertEqual("aira-d4-capstone-py-runtime", capstone_aws.ROLE_NAME)

    def test_a_missing_shared_key_is_a_setup_error(self):
        self.state.write_text("{}")
        with self.assertRaises(capstone_aws.SetupError):
            capstone_aws.environment()

    def test_an_agentcore_run_is_stored_traced_approvable_and_never_applied(self):
        db = os.path.join(TMP, f"ac-{secrets.token_hex(3)}.sqlite")
        sla = responder.sla_mod.compute(HttpOpsReader(OPS.url, OPS.read_tokens["ACC-1001"]), "ACC-1001", T1030)
        record = {"run_id": secrets.token_hex(5), "status": "awaiting_approval", "cost_usd": 0.05, "turns": 3, "tool_calls": 2,
                  "proposal": good(), "verdict": guardrails.verify(good(), sla).to_json(), "sla": sla.to_json(),
                  "trajectory": [["sla_report", {}, True]], "mode": "agentcore", "account_id": "ACC-1001",
                  "as_of": "2026-09-24T10:30:00+05:30",
                  "trace": [{"trace_id": "x", "span_id": "a", "parent_id": None, "name": "run", "start": 1.0,
                             "duration_ms": 5, "status": "ok", "error": None, "attrs": {"account": "ACC-1001"}}]}
        with Store(db) as s:
            path = capstone_aws.record(s, record, "check Sahyadri")
            self.assertTrue(path.read_text().startswith('{"trace_id":"x"'))
            rid = record["run_id"]
            self.assertEqual("agentcore", s.run(rid).mode)
            gate.decide(s, rid, "approve", "Asha Rao", "arn:aws:sts::<account>:assumed-role/x", "numbers match the queue here",
                        repo.spans.Tracer("capstone", trace_id=rid))
            with self.assertRaises(GateError):
                gate.apply(s, rid, None, OPS.url, repo.spans.Tracer("capstone", trace_id=rid))
            out = io.StringIO()
            cli.show(s, rid, out)
            self.assertIn(f"run {rid} · agentcore · ACC-1001", out.getvalue())
            self.assertNotIn("next: apply", out.getvalue(), "an AgentCore run is never applied")


if __name__ == "__main__":
    unittest.main()
