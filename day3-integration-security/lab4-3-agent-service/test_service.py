"""Lab 4.3 service tests. No model: a fake gateway streams scripted replies.

    python3 -m unittest test_service -v
"""
import json, os, secrets, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS = HERE.parent / "aira-ops" / "aira_ops.py"
OPS_TOKEN = secrets.token_hex(16)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def sse_turn(text, tool=None, stop="end_turn", out_tokens=40):
    """One assistant turn as the gateway would stream it."""
    ev = [{"type": "message_start", "message": {"usage": {"input_tokens": 500, "output_tokens": 1}}},
          {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}]
    for w in text.split(" "):
        ev.append({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w + " "}})
    ev.append({"type": "content_block_stop", "index": 0})
    if tool:
        name, args = tool
        ev += [{"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_" + secrets.token_hex(6), "name": name, "input": {}}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ""}},
               {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}},
               {"type": "content_block_stop", "index": 1}]
    ev += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": out_tokens}},
           {"type": "message_stop"}]
    return ev

class FakeGateway:
    """Serves a script of turns in order. delay = seconds between events."""
    def __init__(self):
        self.script, self.delay, self.requests, self.stall_first = [], 0.0, [], 0
        outer = self
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if outer.stall_first > 0:
                    outer.stall_first -= 1
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close"); self.end_headers()
                    first = sse_turn("half an answer that will be thrown away")[:3]
                    for e in first:
                        self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()); self.wfile.flush()
                    time.sleep(2.0)          # go silent: longer than the stall limit
                    self.close_connection = True
                    return
                turn = outer.script.pop(0) if outer.script else sse_turn("done.")
                self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close"); self.end_headers()
                try:
                    for e in turn:
                        self.wfile.write(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()); self.wfile.flush()
                        time.sleep(outer.delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
        self.port = free_port()
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.gw = FakeGateway()
        ops_port = free_port()
        cls.ops = subprocess.Popen([sys.executable, str(OPS), "--port", str(ops_port), "--quiet",
                                    "--db", str(Path(cls.tmp.name) / "o.sqlite")],
                                   env=dict(os.environ, AIRA_OPS_TOKEN=OPS_TOKEN),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(f"http://127.0.0.1:{ops_port}/health", timeout=0.2); break
            except Exception: time.sleep(0.1)
        os.environ.update(ANTHROPIC_BASE_URL=f"http://127.0.0.1:{cls.gw.port}", ANTHROPIC_AUTH_TOKEN="fake",
                          LAB_MODEL="claude-sonnet", AIRA_OPS_TOKEN=OPS_TOKEN,
                          AIRA_OPS_URL=f"http://127.0.0.1:{ops_port}")
        sys.path.insert(0, str(HERE))
        if os.environ.get("LAB43_TARGET") == "starter":     # run the suite against YOUR code
            sys.path.insert(0, str(HERE / "starter"))
        import service
        cls.service = service
        service.OPS_URL, service.OPS_TOKEN = f"http://127.0.0.1:{ops_port}", OPS_TOKEN
        cls.port = free_port()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.port), service.Handler)
        cls.srv.svc = service.Service(db_path=str(Path(cls.tmp.name) / "runs.sqlite"))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.gw.srv.shutdown(); cls.ops.terminate(); cls.ops.wait(); cls.tmp.cleanup()

    def setUp(self):
        s = self.service
        s.RUN_TIMEOUT_S, s.MAX_STEPS, s.RUN_BUDGET_USD, s.STREAM_STALL_S = 30, 8, 0.25, 0.5
        self.gw.script, self.gw.delay, self.gw.requests, self.gw.stall_first = [], 0.0, [], 0

    # helpers
    def start(self, acct="ACC-1001"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/triage", method="POST",
                                     data=json.dumps({"account_id": acct}).encode(),
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["run_id"]

    def events(self, run_id, on_event=None):
        out = []
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/runs/{run_id}/events", timeout=30) as r:
            etype = None
            for raw in r:
                line = raw.decode().rstrip("\n")
                if line.startswith("event:"):
                    etype = line[6:].strip()
                elif line.startswith("data:"):
                    ev = (etype, json.loads(line[5:]))
                    out.append(ev)
                    if on_event: on_event(ev)
        return out

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return json.loads(r.read())

    # --- persistence survives a crash honestly
    def test_run_orphaned_by_a_restart_is_marked_interrupted(self):
        path = str(Path(self.tmp.name) / "restart.sqlite")
        first = self.service.Service(db_path=path)
        with self.service.DB_LOCK:
            first.db.execute("insert into runs values(?,?,?,?,?,?,?,?,?)",
                             ("deadbeef0001", "ACC-1001", "running", "t0", None, 2, 0.01, None, None))
            first.db.commit()
        second = self.service.Service(db_path=path)          # the process came back
        row = second.db.execute("select status, error from runs where id='deadbeef0001'").fetchone()
        self.assertEqual(row[0], "interrupted")
        self.assertIn("restarted", row[1])

    # --- the starter must stay the reference minus one method, or the lab drifts
    def test_starter_differs_from_the_reference_only_in_request_cancel(self):
        import re
        def strip(src):
            src = re.sub(r"    def request_cancel\(self\):.*?(?=    def _on_deadline)", "", src, flags=re.S)
            src = src.split("Lab 4.3 \u2014 an agent inside", 1)[1]           # drop the starter's banner
            return re.sub(r"HERE = .*?\n(sys.path.insert\(0, str\(HERE\)\).*?\n)?", "", src, count=1)
        ref = (HERE / "service.py").read_text(); starter = (HERE / "starter" / "service.py").read_text()
        self.assertEqual(strip(starter), strip(ref))
        self.assertIn("TODO (Lab 4.3)", starter)

    # --- least privilege, checked statically
    def test_agent_has_no_write_tools(self):
        names = {t["name"] for t in self.service.TOOLS}
        self.assertEqual(names, {"search_tickets", "get_ticket", "lookup_account", "get_config"})
        self.assertFalse(any(w in n for n in names for w in ("update", "create", "delete", "add", "set")))

    # --- the happy path, streamed and persisted
    def test_run_streams_tools_and_text_then_persists_the_trace(self):
        self.gw.script = [sse_turn("Checking the account.", ("lookup_account", {"id": "ACC-1001"}), "tool_use"),
                          sse_turn("SLA is 240 minutes. Proposed: review ingest concurrency.")]
        rid = self.start()
        ev = self.events(rid)
        kinds = [k for k, _ in ev]
        self.assertEqual(kinds[0], "status"); self.assertEqual(kinds[-1], "done")
        self.assertLess(kinds.index("tool_call"), kinds.index("tool_result"))
        self.assertGreater(kinds.count("text"), 3, "text must arrive in pieces, not all at once")
        tr = [d for k, d in ev if k == "tool_result"][0]
        self.assertIn("SLA 240 min", tr["summary"])
        self.assertEqual(ev[-1][1]["status"], "done"); self.assertFalse(ev[-1][1]["partial"])
        run = self.get(f"/api/runs/{rid}")
        self.assertEqual(run["status"], "done"); self.assertIn("240", run["answer"])
        self.assertEqual(len(run["events"]), len(ev))
        # the tool result really went back to the model on the second call
        second = self.gw.requests[1]["messages"]
        self.assertEqual(second[-1]["content"][0]["type"], "tool_result")

    # --- cancellation mid-stream, with the partial answer kept
    def test_cancel_mid_stream_keeps_partial_text(self):
        self.gw.delay = 0.05
        self.gw.script = [sse_turn(" ".join(["word"] * 200))]
        rid = self.start()
        def maybe_cancel(ev):
            if ev[0] == "text" and not getattr(self, "_c", False):
                self._c = True
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/api/runs/{rid}/cancel", method="POST", data=b""))
        self._c = False
        ev = self.events(rid, on_event=maybe_cancel)
        done = ev[-1][1]
        self.assertEqual(done["status"], "cancelled"); self.assertTrue(done["partial"])
        run = self.get(f"/api/runs/{rid}")
        self.assertIn("word", run["answer"]); self.assertIn("nothing was changed", run["answer"])
        self.assertLess(sum(1 for k, _ in ev if k == "text"), 200, "cancel must stop the stream early")

    def test_cancel_takes_effect_while_the_model_is_silent(self):
        # The worker is blocked in a socket read, waiting for bytes that are not
        # coming. A flag checked between chunks can't reach it; aborting the stream can.
        self.service.STREAM_STALL_S = 10
        self.gw.stall_first = 1                       # sends a few events, then 2 s of silence
        rid = self.start()
        t = {}
        def on(ev):
            if ev[0] == "text" and "sent" not in t:
                time.sleep(0.3)                       # let the worker block on the silent socket
                t["sent"] = time.monotonic()
                urllib.request.urlopen(urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/api/runs/{rid}/cancel", method="POST", data=b""))
        ev = self.events(rid, on_event=on)
        self.assertEqual(ev[-1][1]["status"], "cancelled")
        self.assertLess(time.monotonic() - t["sent"], 1.0, "cancel waited for the silent stream")

    def test_timeout_fires_while_the_model_is_silent(self):
        self.service.STREAM_STALL_S, self.service.RUN_TIMEOUT_S = 10, 0.5
        self.gw.stall_first = 1
        t0 = time.monotonic()
        ev = self.events(self.start())
        self.assertEqual(ev[-1][1]["status"], "timeout")
        self.assertLess(time.monotonic() - t0, 1.5, "the deadline waited for the silent stream")

    # --- wall-clock timeout
    def test_timeout_stops_a_slow_stream(self):
        self.service.RUN_TIMEOUT_S = 0.6
        self.gw.delay = 0.1
        self.gw.script = [sse_turn(" ".join(["slow"] * 100))]
        t0 = time.monotonic()
        ev = self.events(self.start())
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(ev[-1][1]["status"], "timeout"); self.assertTrue(ev[-1][1]["partial"])

    # --- budget ceiling stops the NEXT call
    def test_budget_is_a_hard_ceiling_no_call_that_could_overshoot_is_made(self):
        # Checking "spent < limit" before a call is a soft threshold: the call
        # itself can blow through it. The run reserves the worst case first.
        self.service.RUN_BUDGET_USD = 0.0001
        self.gw.script = [sse_turn("should never be requested")]
        ev = self.events(self.start())
        self.assertEqual(ev[-1][1]["status"], "budget")
        self.assertEqual(len(self.gw.requests), 0, "a call was made whose worst case exceeded the budget")

    def test_a_stalled_attempt_is_charged_its_reservation(self):
        # We never see the usage of a stream that died, but the gateway may bill it.
        self.service.RUN_BUDGET_USD = 1.0
        self.gw.stall_first = 1
        self.gw.script = [sse_turn("Complete answer.")]
        ev = self.events(self.start())
        self.assertEqual(ev[-1][1]["status"], "done")
        self.assertGreater(ev[-1][1]["cost_usd"], 0.02, "the stalled attempt was treated as free")

    # --- step cap
    def test_step_limit(self):
        self.service.MAX_STEPS = 2
        self.gw.script = [sse_turn("a", ("get_config", {}), "tool_use") for _ in range(5)]
        ev = self.events(self.start())
        self.assertEqual(ev[-1][1]["status"], "step_limit"); self.assertEqual(len(self.gw.requests), 2)

    # --- secrets
    def test_ops_token_never_reaches_the_page_or_the_model(self):
        self.gw.script = [sse_turn("Reading.", ("get_ticket", {"id": "T-1007"}), "tool_use"), sse_turn("Done.")]
        rid = self.start("ACC-1003")
        ev = self.events(rid)
        self.assertNotIn(OPS_TOKEN, json.dumps(ev))
        self.assertNotIn(OPS_TOKEN, json.dumps(self.get(f"/api/runs/{rid}")))
        self.assertNotIn(OPS_TOKEN, json.dumps(self.gw.requests))

    # --- input validation at the boundary
    def test_bad_account_is_rejected_before_any_model_call(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/triage", method="POST",
                                     data=b'{"account_id":"Sahyadri"}', headers={"content-type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400); self.assertEqual(self.gw.requests, [])

    # --- a stalled stream is retried once, without duplicating text
    def test_stalled_stream_is_retried_once_and_text_not_duplicated(self):
        self.gw.stall_first = 1
        self.gw.script = [sse_turn("Final answer after retry.")]
        rid = self.start()
        ev = self.events(rid)
        self.assertEqual([k for k, _ in ev].count("retry"), 1)
        self.assertEqual(ev[-1][1]["status"], "done")
        run = self.get(f"/api/runs/{rid}")
        self.assertIn("Final answer after retry.", run["answer"])
        # The stalled attempt streams exactly one delta, "half ", before going silent.
        # Assert on THAT - asserting on words it never sent would pass vacuously.
        self.assertTrue(any(k == "text" and d["delta"].startswith("half") for k, d in ev),
                        "precondition: the stalled attempt must have streamed some text")
        self.assertNotIn("half", run["answer"], "the stalled attempt's text was kept")

    def test_two_stalls_end_the_run_as_upstream_stall(self):
        self.gw.stall_first = 2
        ev = self.events(self.start())
        self.assertEqual(ev[-1][1]["status"], "upstream_stall"); self.assertTrue(ev[-1][1]["partial"])
        self.assertEqual(len(self.gw.requests), 2, "must retry exactly once, not forever")

if __name__ == "__main__":
    unittest.main()
