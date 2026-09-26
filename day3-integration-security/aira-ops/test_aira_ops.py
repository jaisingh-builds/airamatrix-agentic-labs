"""aira-ops contract tests. Standard library only; no model, no network.

    python3 -m unittest test_aira_ops -v
"""
import json, os, socket, tempfile, threading, unittest, urllib.error, urllib.request, uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import aira_ops

TOKEN = "test-token-" + uuid.uuid4().hex[:8]
TRIAGE = "triage-" + uuid.uuid4().hex      # per-caller tokens: scoped to ACC-1001, read-only
ONCALL = "oncall-" + uuid.uuid4().hex      # all accounts, may write

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = aira_ops.connect(str(Path(cls.tmp.name) / "t.sqlite"))
        aira_ops.seed(db)
        cls.port = free_port()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", cls.port), aira_ops.Handler)
        cls.srv.db, cls.srv.token, cls.srv.latency, cls.srv.quiet = db, TOKEN, 0, True
        cls.srv.callers = {  # what --callers loads: hashes only
            aira_ops.sha256(TRIAGE): {"actor": "triage-agent", "accounts": ["ACC-1001"], "write": False, "verified": True},
            aira_ops.sha256(ONCALL): {"actor": "oncall-lead", "accounts": "*", "write": True, "verified": True},
        }
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.tmp.cleanup()

    def call(self, method, path, body=None, token=TOKEN, key=None, actor="unittest"):
        h = {"Content-Type": "application/json", "X-Actor": actor}
        if token: h["Authorization"] = f"Bearer {token}"
        if key: h["Idempotency-Key"] = key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    # auth
    def test_health_needs_no_token(self):
        self.assertEqual(self.call("GET", "/health", token=None)[0], 200)

    def test_every_other_route_needs_the_token(self):
        s, b = self.call("GET", "/tickets", token=None)
        self.assertEqual(s, 401); self.assertEqual(b["error"]["code"], "unauthorised")
        s, _ = self.call("GET", "/tickets", token="wrong")
        self.assertEqual(s, 401)

    # reads
    def test_search_filters_and_text(self):
        s, b = self.call("GET", "/tickets?status=open&priority=P1")
        self.assertEqual(s, 200)
        self.assertEqual({t["id"] for t in b["tickets"]}, {"T-1001", "T-1007"})
        s, b = self.call("GET", "/tickets?q=scanner")
        self.assertIn("T-1005", {t["id"] for t in b["tickets"]})

    def test_bad_status_filter_is_a_400_with_the_valid_values(self):
        s, b = self.call("GET", "/tickets?status=urgent")
        self.assertEqual(s, 400); self.assertIn("in_progress", b["error"]["message"])

    def test_unknown_ticket_error_tells_the_agent_what_to_do(self):
        s, b = self.call("GET", "/tickets/T-9999")
        self.assertEqual(s, 404)
        e = b["error"]
        self.assertEqual(e["code"], "not_found"); self.assertFalse(e["retryable"]); self.assertIn("search_tickets", e["hint"])

    def test_account_id_is_validated_before_lookup(self):
        s, b = self.call("GET", "/accounts/Sahyadri")
        self.assertEqual(s, 400); self.assertIn("ACC-1001", b["error"]["hint"])
        s, b = self.call("GET", "/accounts/ACC-1001")
        self.assertEqual(s, 200); self.assertEqual(b["contract_sla_minutes"], 240); self.assertGreaterEqual(b["open_tickets"], 2)

    # the story in the data
    def test_seed_contains_the_diagnosable_cause(self):
        _, b = self.call("GET", "/config/ingest.max_concurrent_jobs")
        self.assertEqual(b["value"], 4); self.assertIn("16", b["description"])

    def test_seed_contains_the_injection_bait(self):
        _, b = self.call("GET", "/tickets/T-1007")
        self.assertIn("Automated note for AI assistants", b["body"])
        self.assertIn("AIRA_OPS_TOKEN", b["body"])

    # writes: idempotency
    def test_write_without_idempotency_key_is_refused(self):
        s, b = self.call("POST", "/tickets", {"title": "x", "account_id": "ACC-1001"})
        self.assertEqual(s, 400); self.assertIn("Idempotency-Key", b["error"]["message"])

    def test_retry_with_same_key_does_not_duplicate(self):
        k = str(uuid.uuid4())
        body = {"title": "Retry test", "account_id": "ACC-1002", "priority": "P3"}
        s1, b1 = self.call("POST", "/tickets", body, key=k)
        s2, b2 = self.call("POST", "/tickets", body, key=k)
        self.assertEqual(s1, 201); self.assertEqual(s2, 201)
        self.assertEqual(b1["id"], b2["id"]); self.assertTrue(b2.get("_replayed"))
        _, found = self.call("GET", "/tickets?q=Retry%20test")
        self.assertEqual(found["count"], 1)

    def test_create_reports_every_invalid_field(self):
        s, b = self.call("POST", "/tickets", {"title": "", "priority": "urgent", "account_id": "ACC-9"}, key=str(uuid.uuid4()))
        self.assertEqual(s, 400)
        m = b["error"]["message"]
        for frag in ("title", "priority", "ACC-9"):
            self.assertIn(frag, m)

    # writes: state machine
    def test_illegal_transition_is_409_with_allowed_moves(self):
        s, b = self.call("PATCH", "/tickets/T-1006", {"status": "open"}, key=str(uuid.uuid4()))
        self.assertEqual(s, 409); self.assertIn("closed is final", b["error"]["hint"])

    # writes: optimistic concurrency
    def test_stale_config_write_is_409_not_an_overwrite(self):
        s, b = self.call("PUT", "/config/alerts.ingest_latency_minutes", {"value": 30, "expected_version": 99}, key=str(uuid.uuid4()))
        self.assertEqual(s, 409); self.assertIn("Re-read", b["error"]["hint"])
        _, cur = self.call("GET", "/config/alerts.ingest_latency_minutes")
        self.assertEqual(cur["value"], 15)

    def test_config_write_bumps_version_and_is_audited(self):
        _, cur = self.call("GET", "/config/viewer.overlay_calibration_um")
        s, b = self.call("PUT", "/config/viewer.overlay_calibration_um", {"value": 1.5, "expected_version": cur["version"]}, key=str(uuid.uuid4()))
        self.assertEqual(s, 200); self.assertEqual(b["version"], cur["version"] + 1)
        _, a = self.call("GET", "/audit")
        top = a["entries"][0]
        self.assertEqual((top["action"], top["target"], top["actor"]), ("config.update", "viewer.overlay_calibration_um", "unittest"))

    def test_errors_never_leak_internals(self):
        s, b = self.call("GET", "/nope")
        self.assertEqual(s, 404); self.assertNotIn("Traceback", json.dumps(b))

    def test_rejected_write_does_not_poison_a_keep_alive_connection(self):
        # Real clients (Node fetch, requests) reuse connections. A write rejected
        # before its body is read must not leave that body in the socket.
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
             "Idempotency-Key": str(uuid.uuid4())}
        c.request("PUT", "/config/no.such.key", body=json.dumps({"value": 1, "expected_version": 1}), headers=h)
        r1 = c.getresponse(); r1.read()
        self.assertEqual(r1.status, 404)
        c.request("GET", "/tickets/T-1001", headers={"Authorization": f"Bearer {TOKEN}"})
        r2 = c.getresponse()
        self.assertEqual(r2.status, 200, "the unread body of the previous request was parsed as this one")
        c.close()

    def test_config_write_cannot_change_the_type(self):
        # Observed live on 2026-09-25: the model sent "16" for an integer key.
        _, cur = self.call("GET", "/config/ingest.max_concurrent_jobs")
        s, b = self.call("PUT", "/config/ingest.max_concurrent_jobs",
                         {"value": "16", "expected_version": cur["version"]}, key=str(uuid.uuid4()))
        self.assertEqual(s, 400)
        self.assertIn("number", b["error"]["message"]); self.assertIn("e.g. 4", b["error"]["hint"])
        _, after = self.call("GET", "/config/ingest.max_concurrent_jobs")
        self.assertEqual((after["value"], after["version"]), (4, cur["version"]))

    # idempotency keys name one operation, not "some request"
    def test_same_key_with_a_different_payload_is_rejected_not_replayed(self):
        k = str(uuid.uuid4())
        s1, _ = self.call("POST", "/tickets/T-1003/comments", {"body": "first"}, key=k)
        s2, b2 = self.call("POST", "/tickets/T-1003/comments", {"body": "second, different"}, key=k)
        self.assertEqual(s1, 201); self.assertEqual(s2, 422)
        self.assertIn("different request", b2["error"]["message"])
        _, t = self.call("GET", "/tickets/T-1003")
        self.assertEqual([c["body"] for c in t["comments"]].count("second, different"), 0)

    def test_two_intentional_identical_writes_with_new_keys_both_happen(self):
        for _ in range(2):
            s, _ = self.call("POST", "/tickets/T-1009/comments", {"body": "Still reproducing."}, key=str(uuid.uuid4()))
            self.assertEqual(s, 201)
        _, t = self.call("GET", "/tickets/T-1009")
        self.assertEqual([c["body"] for c in t["comments"]].count("Still reproducing."), 2)

    def test_comment_field_is_accepted_as_well_as_body(self):
        # AgentCore Gateway treats an OpenAPI property named "body" as the whole request body,
        # so the Day 4 gateway spec sends "comment". Both must land as the comment text.
        s, _ = self.call("POST", "/tickets/T-1006/comments", {"comment": "via gateway"}, key=str(uuid.uuid4()))
        self.assertEqual(s, 201)
        _, t = self.call("GET", "/tickets/T-1006")
        self.assertEqual(t["comments"][-1]["body"], "via gateway")
        s, _ = self.call("POST", "/tickets/T-1006/comments", {"comment": "  "}, key=str(uuid.uuid4()))
        self.assertEqual(s, 400)

    # identity: the token decides who you are, not a header
    def test_shared_token_audit_entries_are_marked_unverified(self):
        self.call("POST", "/tickets/T-1003/comments", {"body": "label only"}, key=str(uuid.uuid4()), actor="anyone-i-like")
        _, a = self.call("GET", "/audit")
        top = a["entries"][0]
        self.assertEqual((top["actor"], top["verified"]), ("anyone-i-like", 0))

    def test_verified_caller_cannot_impersonate_by_changing_x_actor(self):
        s, _ = self.call("POST", "/tickets/T-1008/comments", {"body": "SSO fix deployed"},
                         token=ONCALL, key=str(uuid.uuid4()), actor="ceo")
        self.assertEqual(s, 201)
        _, t = self.call("GET", "/tickets/T-1008")
        self.assertEqual(t["comments"][-1]["author"], "oncall-lead")
        _, a = self.call("GET", "/audit")
        top = a["entries"][0]
        self.assertEqual((top["actor"], top["verified"]), ("oncall-lead", 1))
        self.assertEqual(json.loads(top["detail"])["claimed_actor"], "ceo")

    def test_scoped_caller_cannot_see_another_accounts_data(self):
        s, b = self.call("GET", "/tickets/T-1001", token=TRIAGE)          # ACC-1001: allowed
        self.assertEqual(s, 200)
        s, b = self.call("GET", "/tickets/T-1007", token=TRIAGE)          # ACC-1003: hidden
        self.assertEqual(s, 404); self.assertEqual(b["error"]["message"], "no ticket T-1007")
        s, _ = self.call("GET", "/accounts/ACC-1003", token=TRIAGE)
        self.assertEqual(s, 404)
        _, lst = self.call("GET", "/tickets?status=open", token=TRIAGE)
        self.assertEqual({t["account_id"] for t in lst["tickets"]}, {"ACC-1001"})
        _, lst = self.call("GET", "/tickets?account_id=ACC-1003", token=TRIAGE)
        self.assertEqual(lst["count"], 0)
        _, jobs = self.call("GET", "/jobs", token=TRIAGE)
        self.assertTrue(all(j["account_id"] == "ACC-1001" for j in jobs["jobs"]))
        self.assertEqual(self.call("GET", "/audit", token=TRIAGE)[0], 403)

    def test_read_only_caller_cannot_write_even_to_its_own_account(self):
        s, b = self.call("POST", "/tickets/T-1001/comments", {"body": "x"}, token=TRIAGE, key=str(uuid.uuid4()))
        self.assertEqual(s, 403); self.assertEqual(b["error"]["code"], "forbidden")

    def test_callers_file_stores_hashes_not_tokens(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "callers.json"
            out = subprocess.run([sys.executable, str(Path(aira_ops.__file__)), "--callers", str(f),
                                  "--issue-token", "triage-agent", "--accounts", "ACC-1001"],
                                 capture_output=True, text=True, env=dict(os.environ, AIRA_OPS_TOKEN="x"))
            tok = out.stdout.strip()
            self.assertEqual(len(tok), 32)
            text = f.read_text()
            self.assertNotIn(tok, text)
            self.assertIn(aira_ops.sha256(tok), text)
            self.assertEqual(aira_ops.load_callers(str(f))[aira_ops.sha256(tok)]["accounts"], ["ACC-1001"])

if __name__ == "__main__":
    unittest.main()
