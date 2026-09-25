"""End-to-end tests for the aira-ops MCP server, through the real protocol.

Starts aira-ops on a free port, launches server.ts as a stdio child exactly as
Claude Code does, and drives it with the Python client. No model, no gateway.

    python3 -m unittest test_mcp_server -v
"""
import json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.request, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE.parent / "aira-ops"
sys.path.insert(0, str(HERE))
from client import McpClient

TOKEN = "tok-" + uuid.uuid4().hex

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def text(result):
    return result["content"][0]["text"]

class McpE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        env = dict(os.environ, AIRA_OPS_TOKEN=TOKEN)
        cls.ops = subprocess.Popen([sys.executable, str(OPS / "aira_ops.py"), "--port", str(cls.port),
                                    "--db", str(Path(cls.tmp.name) / "e2e.sqlite"), "--quiet"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health", timeout=0.2); break
            except Exception:
                time.sleep(0.1)
        cls.env = dict(os.environ, AIRA_OPS_TOKEN=TOKEN, AIRA_OPS_URL=f"http://127.0.0.1:{cls.port}")
        cls.c = McpClient(env=cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.c.close(); cls.ops.terminate(); cls.ops.wait(); cls.tmp.cleanup()

    # --- discovery: what the model actually sees
    def test_handshake(self):
        self.assertEqual(self.c.info["serverInfo"]["name"], "aira-ops")

    def test_at_least_three_tools_with_honest_annotations(self):
        tools = {t["name"]: t for t in self.c.request("tools/list")["tools"]}
        self.assertGreaterEqual(len(tools), 3)
        for read in ("search_tickets", "get_ticket", "lookup_account", "get_config"):
            self.assertTrue(tools[read]["annotations"]["readOnlyHint"], read)
        for write in ("add_ticket_comment", "update_ticket_status", "update_config"):
            self.assertFalse(tools[write]["annotations"]["readOnlyHint"], write)
        self.assertTrue(tools["update_config"]["annotations"]["destructiveHint"])

    def test_every_tool_that_is_not_read_only_is_gated_in_claude_code(self):
        # The approval gate is only as good as its list. A write tool added next
        # month and forgotten here would run unasked - this test is what notices.
        settings = json.loads((HERE / ".claude" / "settings.json").read_text())["permissions"]
        allow, ask = set(settings.get("allow", [])), set(settings.get("ask", []))
        for t in self.c.request("tools/list")["tools"]:
            rule = f"mcp__aira-ops__{t['name']}"
            if t.get("annotations", {}).get("readOnlyHint"):
                self.assertNotIn(rule, ask, f"{t['name']} is read-only; no need to ask")
            else:
                self.assertIn(rule, ask, f"{t['name']} can write but is not in permissions.ask")
                self.assertNotIn(rule, allow, f"{t['name']} can write but is pre-approved in permissions.allow")

    def test_schema_constrains_ids_and_enums(self):
        tools = {t["name"]: t for t in self.c.request("tools/list")["tools"]}
        s = tools["search_tickets"]["inputSchema"]["properties"]
        self.assertEqual(set(s["status"]["enum"]), {"open", "in_progress", "resolved", "closed"})
        self.assertEqual(tools["get_ticket"]["inputSchema"]["properties"]["id"]["pattern"], r"^T-\d{4}$")

    def test_resources_and_prompt_are_offered(self):
        self.assertIn("aira://config", [r["uri"] for r in self.c.request("resources/list")["resources"]])
        cfg = self.c.request("resources/read", {"uri": "aira://config"})
        self.assertIn("ingest.max_concurrent_jobs", cfg["contents"][0]["text"])
        t = self.c.request("resources/read", {"uri": "aira://tickets/T-1001"})
        self.assertIn("backlog", t["contents"][0]["text"])
        p = self.c.request("prompts/get", {"name": "triage", "arguments": {"account_id": "ACC-1001"}})
        self.assertIn("ACC-1001", p["messages"][0]["content"]["text"])

    # --- reads
    def test_search_then_get(self):
        r = self.c.call("search_tickets", {"status": "open", "priority": "P1"})
        ids = {t["id"] for t in json.loads(text(r))["tickets"]}
        self.assertEqual(ids, {"T-1001", "T-1007"})
        r = self.c.call("get_ticket", {"id": "T-1001"})
        self.assertIn("concurrency cap", text(r))

    # --- the error contract, as the model receives it
    def test_schema_rejects_malformed_id_before_any_http_call(self):
        r = self.c.call("get_ticket", {"id": "1001"})
        self.assertTrue(r.get("isError"))

    def test_upstream_error_reaches_model_with_a_hint(self):
        r = self.c.call("get_ticket", {"id": "T-9999"})
        self.assertTrue(r["isError"])
        err = json.loads(text(r))["error"]
        self.assertEqual(err["code"], "not_found"); self.assertIn("search_tickets", err["hint"])

    # --- writes
    def test_two_deliberate_identical_comments_are_two_operations(self):
        # Keys come from the operation, not the arguments: saying the same thing
        # twice on purpose is two writes, and neither is reported as a replay.
        args = {"id": "T-1003", "body": "Acknowledged - reviewer role is on the roadmap."}
        a = json.loads(text(self.c.call("add_ticket_comment", args)))
        b = json.loads(text(self.c.call("add_ticket_comment", args)))
        self.assertFalse(b.get("_replayed"))
        self.assertEqual(len(b["comments"]), len(a["comments"]) + 1)

    def test_write_with_unknown_outcome_can_be_retried_safely_by_operation_id(self):
        # A slow aira-ops: every call takes 0.8 s, the MCP server gives up at 0.3 s.
        # The write lands anyway - exactly the ambiguous case idempotency exists for.
        port = free_port()
        slow = subprocess.Popen([sys.executable, str(OPS / "aira_ops.py"), "--port", str(port), "--latency", "0.8",
                                 "--db", str(Path(self.tmp.name) / "slow.sqlite"), "--quiet"],
                                env=dict(os.environ, AIRA_OPS_TOKEN=TOKEN), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try: urllib.request.urlopen(url + "/health", timeout=0.2); break
            except Exception: time.sleep(0.1)
        impatient = McpClient(env=dict(self.env, AIRA_OPS_URL=url, AIRA_OPS_TIMEOUT_MS="300"))
        patient = McpClient(env=dict(self.env, AIRA_OPS_URL=url, AIRA_OPS_TIMEOUT_MS="5000"))
        try:
            r = impatient.call("add_ticket_comment", {"id": "T-1009", "body": "Export fix is in QA."})
            err = json.loads(text(r))["error"]
            self.assertTrue(r["isError"]); self.assertEqual(err["code"], "outcome_unknown")
            op = err["operation_id"]; self.assertIn(op, err["hint"])
            time.sleep(1.0)                                    # the slow server finishes the write anyway
            again = json.loads(text(patient.call("add_ticket_comment",
                                                 {"id": "T-1009", "body": "Export fix is in QA.", "idempotency_key": op})))
            self.assertTrue(again.get("_replayed"))
            self.assertEqual([c["body"] for c in again["comments"]].count("Export fix is in QA."), 1)
        finally:
            impatient.close(); patient.close(); slow.terminate(); slow.wait()

    def test_stale_config_write_comes_back_as_a_recoverable_conflict(self):
        r = self.c.call("update_config", {"key": "alerts.ingest_latency_minutes", "value": 30, "expected_version": 42})
        self.assertTrue(r["isError"])
        err = json.loads(text(r))["error"]
        self.assertEqual(err["code"], "conflict"); self.assertIn("Re-read", err["hint"])

    def test_illegal_transition_lists_allowed_moves(self):
        r = self.c.call("update_ticket_status", {"id": "T-1006", "status": "in_progress"})
        self.assertTrue(r["isError"]); self.assertIn("closed is final", text(r))

    # --- secrets
    def test_token_never_appears_in_any_result(self):
        outs = [self.c.call("get_config", {}), self.c.call("get_ticket", {"id": "T-1007"}),
                self.c.call("get_ticket", {"id": "T-9999"}),
                self.c.call("update_config", {"key": "nope", "value": 1, "expected_version": 1})]
        for o in outs:
            self.assertNotIn(TOKEN, json.dumps(o))

    def test_server_refuses_to_start_without_a_token(self):
        env = {k: v for k, v in self.env.items() if k != "AIRA_OPS_TOKEN"}
        p = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(HERE / "server.ts")],
                           env=env, input="", capture_output=True, text=True, timeout=15)
        self.assertNotEqual(p.returncode, 0); self.assertIn("Never hardcode", p.stderr)

    def test_read_only_server_refuses_writes_itself(self):
        ro = McpClient(env=dict(self.env, AIRA_OPS_READONLY="1"))
        try:
            r = ro.call("update_config", {"key": "ingest.max_concurrent_jobs", "value": 0, "expected_version": 1})
            self.assertTrue(r.get("isError"), "a read-only server applied a write"); self.assertIn("read_only", text(r))
            self.assertFalse(ro.call("get_config", {"key": "ingest.max_concurrent_jobs"}).get("isError"))
        finally:
            ro.close()

    def test_second_client_asks_before_a_write_and_refuses_without_a_human(self):
        # Claude Code's settings.json does not govern client.py - it needs its own gate.
        before = json.loads(text(self.c.call("get_ticket", {"id": "T-1004"})))
        r = subprocess.run([sys.executable, str(HERE / "client.py"), "add_ticket_comment",
                            json.dumps({"id": "T-1004", "body": "should not land"})],
                           env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not approved", r.stderr)
        after = json.loads(text(self.c.call("get_ticket", {"id": "T-1004"})))
        self.assertEqual(len(after["comments"]), len(before["comments"]))
        ok = subprocess.run([sys.executable, str(HERE / "client.py"), "get_ticket", json.dumps({"id": "T-1004"})],
                            env=self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(ok.returncode, 0)

    def test_unreachable_api_is_retryable_not_a_crash(self):
        dead = McpClient(env=dict(self.env, AIRA_OPS_URL="http://127.0.0.1:1", AIRA_OPS_TIMEOUT_MS="1500"))
        try:
            r = dead.call("get_ticket", {"id": "T-1001"})
            err = json.loads(text(r))["error"]
            self.assertTrue(r["isError"]); self.assertTrue(err["retryable"])
        finally:
            dead.close()

if __name__ == "__main__":
    unittest.main()
