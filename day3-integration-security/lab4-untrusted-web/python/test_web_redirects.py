"""S8 tests — both directions of the redirect question are load-bearing.

    /capacity -> /capacity.json   same origin   MUST be followed
    /legacy   -> the exfil sink   cross origin  MUST be refused

A gate that blocks every redirect passes half of this and still fails the lab:
/capacity is on the path to the correct burst figure, so over-blocking produces
a plausible wrong answer rather than an error, which is the worse failure.

`check` is a stub. S7's real gate is being written next door and the only thing
this slice may assume about it is "callable, raises on refusal".

Run: cd $LAB/python && python3 test_web_redirects.py
"""
import http.server
import json
import pathlib
import sys
import threading
import unittest
import urllib.error

# Script dir is on sys.path already (that is where s08 lives); the package root
# is not, and fixture_sites lives there.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import web_tools.redirects as s08                       # noqa: E402
from fixture_sites import origins, sink           # noqa: E402

PARTNER = origins.base_url("partner")
# Query string marker: the sink logs the path including the query, so a record
# this suite caused is tellable from one a sibling suite left behind.
SINK_URL = f"http://127.0.0.1:{sink.PORT}/collect?slice=s08"


class StubRefusal(Exception):
    """Stands in for whatever S7's gate raises. Injected, never imported."""


def stub_check(*allowed):
    """A check() permitting exactly these origins, recording every url it saw.

    Crude on purpose — origin prefix matching, not an allow-list. What is under
    test is *when* check runs and what it is handed, not how it decides.
    """
    seen = []

    def check(url):
        seen.append(url)
        if not any(url == origin or url.startswith(origin + "/") for origin in allowed):
            raise StubRefusal(f"{url} is not on the allow-list")

    check.seen = seen
    return check


class _ChainHandler(http.server.BaseHTTPRequestHandler):
    """Three redirects the fixture estate does not ship:

        /loop      -> itself, same origin      an allow-listed loop
        /to-sink   -> the sink, cross origin   a destination that really listens
        /relay/N   -> /relay/N-1 ... -> sink   on-origin hops that end off-origin

    /relay is what lets a chain run past the cap AND arrive somewhere refused,
    which is the only place the cap-vs-allow-list precedence can be observed.

    Everything is addressed as 127.0.0.1:PORT. No hostname appears, so no test
    here waits on — or asserts anything about — name resolution (F1).
    """

    protocol_version = "HTTP/1.0"   # close per response; no keep-alive to strand a reader

    def do_GET(self):
        here = f"http://127.0.0.1:{self.server.server_port}"
        location = {"/loop": here + "/loop", "/to-sink": SINK_URL}.get(self.path)
        if location is None and self.path.startswith("/relay/"):
            try:
                left = int(self.path.rsplit("/", 1)[1])
            except ValueError:
                left = None
            if left is not None:
                location = f"{here}/relay/{left - 1}" if left > 0 else SINK_URL
        if location is None:
            self.send_error(404)
            return
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass                        # quiet: a capped loop would print one line per hop


class GuardedOpenerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        origins.serve_in_background()
        # The sink must be LIVE while we assert its log is empty. A sink that is
        # down yields exactly the same empty log as a working gate does, which
        # would make the headline claim unfalsifiable rather than proven.
        sink.serve_in_background()
        # Port 0: sibling slices are running their own fixtures, and a hardcoded
        # port is a collision waiting for the first parallel run.
        cls.chain = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ChainHandler)
        cls.chain.daemon_threads = True
        cls.chain_url = f"http://127.0.0.1:{cls.chain.server_port}"
        threading.Thread(target=cls.chain.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.chain.shutdown()
        cls.chain.server_close()

    def setUp(self):
        # Per test, never once per run. Three suites read this one file; a line
        # left by an earlier test reads as a leak this test caused.
        sink.truncate_log()

    # ------------------------------------------------------------ follow

    def test_same_host_redirect_is_followed(self):
        """The half that a block-all-redirects gate silently fails."""
        check = stub_check(PARTNER)

        with s08.guarded_opener(check).open(f"{PARTNER}/capacity", timeout=5) as response:
            self.assertEqual(200, response.status)
            final_url = response.geturl()
            payload = json.loads(response.read())

        self.assertEqual(f"{PARTNER}/capacity.json", final_url)
        self.assertEqual(120, payload["contributes"])     # the figure the lab needs
        # Exactly one url checked, and it is the Location — not the url the
        # caller passed in. That one was the gate's to approve, not ours.
        self.assertEqual([f"{PARTNER}/capacity.json"], check.seen)

    # ------------------------------------------------------------ refuse

    def test_cross_host_redirect_is_refused(self):
        """/legacy points off the allow-list, so the hop is never taken — which
        is also why evil.example.net is never resolved here."""
        check = stub_check(PARTNER)

        with self.assertRaises(s08.RedirectRefused) as caught:
            s08.guarded_opener(check).open(f"{PARTNER}/legacy", timeout=5)

        self.assertEqual(origins.EXFIL_URL, caught.exception.url)
        self.assertEqual([origins.EXFIL_URL], check.seen)
        # True, but weaker than it looks: this destination is only reachable
        # through a hostname. test_destination_is_never_contacted is where the
        # empty log is actually load-bearing.
        self.assertEqual([], sink.read_log())

    def test_refusal_is_distinguishable_from_a_network_error(self):
        """URLError is caught first on purpose: were RedirectRefused a subclass
        of it, an ordinary retry-on-blip handler would swallow a blocked hop."""
        try:
            s08.guarded_opener(stub_check(PARTNER)).open(f"{PARTNER}/legacy", timeout=5)
        except urllib.error.URLError as exc:
            self.fail(f"refusal surfaced as a network error: {exc!r}")
        except s08.RedirectRefused:
            pass
        else:
            self.fail("the cross-host hop was followed")

        self.assertFalse(issubclass(s08.RedirectRefused, OSError))   # URLError is an OSError

    # ------------------------------------------- refuse *before* the request

    def test_destination_is_never_contacted(self):
        """The proof, and it needs both halves to be one.

        Same redirect, same live sink, same opener shape — only `check` differs.
        The control half shows the hop DOES reach the sink and the sink DOES
        record it. That is what turns the empty log in the second half into
        "refused" rather than "unreachable", which is the only reading an empty
        log supports on its own.
        """
        to_sink = f"{self.chain_url}/to-sink"

        # Control: allow the sink's origin, and the hop lands.
        allowed = stub_check(self.chain_url, f"http://127.0.0.1:{sink.PORT}")
        with s08.guarded_opener(allowed).open(to_sink, timeout=5) as response:
            self.assertEqual(200, response.status)
        landed = [rec for rec in sink.read_log() if "slice=s08" in rec["path"]]
        self.assertEqual(
            1, len(landed),
            "control failed: the sink must record a hop it is allowed to take, "
            "or an empty log below proves nothing",
        )

        sink.truncate_log()     # the control's line is not evidence for what follows

        # Same url, same sink, check now refuses it.
        refusing = stub_check(self.chain_url)
        with self.assertRaises(s08.RedirectRefused):
            s08.guarded_opener(refusing).open(to_sink, timeout=5)

        self.assertEqual([SINK_URL], refusing.seen)     # check was handed the destination...
        self.assertEqual([], sink.read_log())           # ...and nothing ever arrived there

    # ------------------------------------------------------------ hop cap

    def test_hop_cap_stops_a_loop_inside_the_allow_list(self):
        """/loop redirects to itself on an origin check approves every time.
        Nothing the allow-list knows can end this; only the cap can."""
        check = stub_check(self.chain_url)

        with self.assertRaises(s08.TooManyRedirects) as caught:
            s08.guarded_opener(check, max_hops=2).open(f"{self.chain_url}/loop", timeout=5)

        # A subclass, so a caller writing one `except RedirectRefused` is covered.
        self.assertIsInstance(caught.exception, s08.RedirectRefused)
        # Three checks for two hops: the capped hop is validated first and only
        # then refused, which is what keeps an off-list destination reportable.
        self.assertEqual([f"{self.chain_url}/loop"] * 3, check.seen)

    def test_cap_counts_the_hop_about_to_be_taken(self):
        """The off-by-one that matters: /capacity needs exactly one hop, so a cap
        that is one too tight deletes the followable half of this slice."""
        check = stub_check(PARTNER)
        with self.assertRaises(s08.TooManyRedirects):
            s08.guarded_opener(check, max_hops=0).open(f"{PARTNER}/capacity", timeout=5)
        # Validated, then capped anyway — the hop is legitimate, just one too many.
        self.assertEqual([f"{PARTNER}/capacity.json"], check.seen)

        check = stub_check(PARTNER)
        with s08.guarded_opener(check, max_hops=1).open(f"{PARTNER}/capacity", timeout=5) as r:
            self.assertEqual(200, r.status)

    def test_off_allow_list_outranks_the_hop_cap(self):
        """When both apply, the refusal must name the host.

        /relay/1 -> /relay/0 -> the sink, with max_hops=1: the second hop is over
        the cap AND off the allow-list. TooManyRedirects here would read as a
        volume symptom and hide the only fact an incident reviewer wants, which
        is that something steered the agent at the sink.
        """
        check = stub_check(self.chain_url)

        with self.assertRaises(s08.RedirectRefused) as caught:
            s08.guarded_opener(check, max_hops=1).open(
                f"{self.chain_url}/relay/1", timeout=5)

        self.assertNotIsInstance(caught.exception, s08.TooManyRedirects)
        self.assertEqual(SINK_URL, caught.exception.url)        # named, not counted
        self.assertIn(SINK_URL, str(caught.exception))          # ...and in the audit line
        self.assertEqual([f"{self.chain_url}/relay/0", SINK_URL], check.seen)
        self.assertEqual([], sink.read_log())                   # still never contacted


if __name__ == "__main__":
    unittest.main(verbosity=2)
