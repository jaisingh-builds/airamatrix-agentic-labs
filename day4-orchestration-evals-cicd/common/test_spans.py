"""What the trace sink redacts - and what it must leave alone. python3 -m unittest test_spans"""
import json, os, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spans import Tracer, redact  # noqa: E402

class RedactTests(unittest.TestCase):
    def test_secrets_by_shape(self):
        fake_key = "sk-" + "live_ABCDEFGH1234"      # built at runtime so repo secret scanners don't flag a test
        s = redact("Authorization: Bearer abcdefgh12345678 and " + fake_key + " and " + "a" * 40)
        for leak in ("abcdefgh12345678", fake_key, "a" * 40):
            self.assertNotIn(leak, s)

    def test_secrets_by_name(self):
        r = redact({"AIRA_OPS_TOKEN": "x", "api_key": "y", "LAB_GATEWAY_KEY": "z", "Authorization": "w", "password": "p"})
        self.assertEqual(set(r.values()), {"[REDACTED]"})

    def test_the_value_of_a_secret_env_var_is_masked_anywhere(self):
        os.environ["LAB_TEST_TOKEN"] = "plain-looking-value-42"
        try:
            self.assertEqual(redact({"note": "sent plain-looking-value-42 to x"})["note"], "sent [REDACTED] to x")
        finally:
            del os.environ["LAB_TEST_TOKEN"]

    def test_ordinary_fields_survive(self):
        # a trace you can't read is a trace you can't debug from
        r = redact({"key": "ingest.max_concurrent_jobs", "keys": 3, "monkey": "ok", "id": "T-1001", "op_id": "5507812a-d2f0"})
        self.assertEqual(r, {"key": "ingest.max_concurrent_jobs", "keys": 3, "monkey": "ok", "id": "T-1001", "op_id": "5507812a-d2f0"})

    def test_long_values_are_cut_in_traces_but_not_when_asked(self):
        self.assertLess(len(redact("x" * 5000)), 700)
        self.assertEqual(len(redact("x" * 5000, limit=None)), 5000)

    def test_redaction_happens_at_the_sink(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Tracer("t", trace_id="abc", root=d)
            with tr.span("call", headers={"Authorization": "Bearer abcdefgh12345678"}):
                pass
            self.assertNotIn("abcdefgh12345678", tr.path.read_text())

    def test_error_messages_are_redacted_before_they_are_cut(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Tracer("t", trace_id="err", root=d)
            try:
                with tr.span("call"):
                    raise RuntimeError("upstream said: Authorization: Bearer abcdefgh12345678 " + "x" * 1000)
            except RuntimeError:
                pass
            rec = json.loads(tr.path.read_text().splitlines()[-1])
            self.assertNotIn("abcdefgh12345678", rec["error"])
            self.assertLess(len(rec["error"]), 400)

    def test_only_allowlisted_attributes_and_identifiers_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            tr = Tracer("t", trace_id="min", root=d)
            tr.event("tool_call", tool="get_ticket", input={"id": "T-1001", "query": "patient Jane Doe, 54, stain HER2"},
                     ticket_body="Patient age 54 ...")
            attrs = json.loads(tr.path.read_text().splitlines()[-1])["attrs"]
            self.assertEqual(attrs["input"], {"id": "T-1001", "query": "[text: 32 chars]"})
            self.assertEqual(attrs["ticket_body"], "[dropped]")

if __name__ == "__main__":
    unittest.main()
