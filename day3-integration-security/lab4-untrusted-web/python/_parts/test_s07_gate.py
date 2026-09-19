#!/usr/bin/env python3
"""S7 offline checks for check_url — the refusal table IS the specification.

    cd day3-integration-security/lab4-untrusted-web/python
    python3 _parts/test_s07_gate.py

Every row asserts the refusal *code*, never merely that something raised: a
check_url that refuses everything passes a table that only checks "raised". The
positive cases carry the other half of the specification and are load-bearing
for exactly that reason.

No test here resolves a hostname. `.local` names cost five seconds each to fail
on macOS, and resolve anyway on a network that hijacks NXDOMAIN — so the suite
would be both slow and wrong. TestNoNetwork pins that property on the module.
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent

# Explicit, not relying on sys.path[0]: that only happens to be _parts/ while
# this file is run as a script, and S12 imports it from python/ instead.
sys.path.insert(0, str(HERE))

from s07_gate import (ALLOWED, REFUSAL_CODES, PolicyRefusal,  # noqa: E402
                      _normalise_host, check_url)

STATUS = "http://status.airamatrix.local:8141/ingest/status.json"
DOCS = "http://docs.airamatrix.local:8142/sla/ingest.html"
PARTNER = "http://partner.example.com:8143/capacity.json"

# The homograph is written as an escape on purpose. Pasted as a raw glyph it is
# invisible in a diff and in a review — which is the attack, not a detail of it.
CYRILLIC_A = "а"
HOMOGRAPH = f"http://p{CYRILLIC_A}rtner.example.com:8143/"

REFUSALS = [
    # url, expected code, why it must fail
    ("http://evil.example.net:8144/collect", "host",
     "not on the list - the base case"),
    ("http://partner.example.com.evil.net:8144/", "host",
     "suffix match is not a match"),
    ("http://evil.net/?x=partner.example.com", "host",
     "substring match is not a match"),
    ("http://partner.example.com@evil.example.net:8144/", "malformed",
     "userinfo - the real host is after the '@'"),
    ("http://partner.example.com:9999/", "port",
     "right host, wrong port - the port is part of identity"),
    ("http://127.0.0.1:8143/", "host",
     "IP literal bypassing the name"),
    ("http://0x7f.0.0.1:8143/", "host",
     "the same, obfuscated - and ip_address cannot even parse it"),
    ("http://[::1]:8143/", "host",
     "the same, obfuscated"),
    ("file:///etc/passwd", "scheme", "scheme is not http(s)"),
    ("gopher://x/", "scheme", "scheme is not http(s)"),
    ("data:text/html,x", "scheme", "scheme is not http(s)"),
    (HOMOGRAPH, "host", "IDNA-normalise before comparing"),
    ("http://xn--prtner-3nf.example.com:8143/", "host",
     "the homograph already in A-label form - same string, same refusal"),
    ("http://partner.example.com/", "port",
     "no port means 80, and 80 is not on the list"),
    ("https://partner.example.com:8143/", "scheme",
     "right host and port, but the origin is http"),
    ("/sla/ingest.html", "malformed",
     "a relative href scraped off a page is not a request yet"),
    ("http://:8143/", "malformed", "no host at all"),
    ("http://example.com:99999/", "malformed", "port out of range"),
    ("http://partner.example.com\n:8143/", "malformed",
     "urlsplit would strip the newline; the gate must see it first"),
    ("http://user@partner.example.com:8143/", "malformed",
     "userinfo is refused even when the host behind it is allowed"),
    ("", "malformed", "empty input"),
]


def refuse(url):
    """Call check_url expecting a refusal; return it. Fails loudly if it passed."""
    try:
        allowed = check_url(url)
    except PolicyRefusal as exc:
        return exc
    raise AssertionError(f"{url!r} was ALLOWED as {allowed!r}")


class TestRefusalTable(unittest.TestCase):
    """Every row refused, and refused for the stated reason."""

    def test_every_row_is_refused_with_the_right_code(self):
        for url, code, why in REFUSALS:
            with self.subTest(url=url, why=why):
                self.assertEqual(refuse(url).code, code)

    def test_every_code_is_from_the_fixed_set(self):
        """The contract slice indexes on these; an ad-hoc code breaks it."""
        for url, _, _ in REFUSALS:
            with self.subTest(url=url):
                self.assertIn(refuse(url).code, REFUSAL_CODES)

    def test_refusal_carries_both_code_and_message(self):
        exc = refuse("http://evil.example.net:8144/collect")
        self.assertIsInstance(exc.code, str)
        self.assertIsInstance(exc.message, str)
        self.assertEqual(str(exc), exc.message)

    def test_an_unknown_code_cannot_be_constructed(self):
        with self.assertRaises(ValueError):
            PolicyRefusal("oops", "not a real code")


class TestRefusalMessages(unittest.TestCase):
    """The agent reads these and must be able to act on them."""

    def test_host_refusal_names_the_host_and_the_reachable_list(self):
        exc = refuse("http://evil.example.net:8144/collect")
        self.assertIn("evil.example.net", exc.message)
        for _, host, port in ALLOWED:
            self.assertIn(f"{host}:{port}", exc.message)

    def test_userinfo_refusal_names_the_host_actually_reached(self):
        """Naming 'partner' here would teach exactly the wrong lesson."""
        exc = refuse("http://partner.example.com@evil.example.net:8144/")
        self.assertIn("evil.example.net", exc.message)

    def test_port_refusal_names_the_port_that_would_work(self):
        exc = refuse("http://partner.example.com:9999/")
        self.assertIn("8143", exc.message)
        self.assertIn("9999", exc.message)

    def test_implicit_port_refusal_names_80(self):
        """The fix is 'add :8143', so the message has to show what it read."""
        exc = refuse("http://partner.example.com/")
        self.assertIn("80", exc.message)
        self.assertIn("8143", exc.message)

    def test_ip_literal_refusal_says_the_gate_matches_names(self):
        self.assertIn("IP literal", refuse("http://127.0.0.1:8143/").message)


class TestAllowed(unittest.TestCase):
    """One per origin. A gate that refuses everything must fail here."""

    def test_status_origin(self):
        self.assertEqual(check_url(STATUS),
                         ("http", "status.airamatrix.local", 8141))

    def test_docs_origin(self):
        self.assertEqual(check_url(DOCS),
                         ("http", "docs.airamatrix.local", 8142))

    def test_partner_origin(self):
        self.assertEqual(check_url(PARTNER),
                         ("http", "partner.example.com", 8143))

    def test_every_allow_list_entry_round_trips(self):
        """Catches an entry spelled in a form the normaliser would not produce."""
        for scheme, host, port in ALLOWED:
            with self.subTest(host=host):
                self.assertEqual(check_url(f"{scheme}://{host}:{port}/"),
                                 (scheme, host, port))


class TestNormalisationMatchesTheRightHost(unittest.TestCase):
    """Normalisation has to make the correct host MATCH, not just reject."""

    def test_shouted_host_with_root_dot_is_the_partner_origin(self):
        self.assertEqual(check_url("http://PARTNER.example.com.:8143/x"),
                         ("http", "partner.example.com", 8143))

    def test_same_host_fails_on_the_port_alone(self):
        """Proves the pass above came from normalising, not from a loose match."""
        self.assertEqual(refuse("http://PARTNER.Example.COM.:9999/x").code, "port")

    def test_returned_triple_is_normalised_not_the_input_spelling(self):
        """Callers connect to what was checked; returning the raw text reopens
        the whole hole this gate closes."""
        scheme, host, port = check_url("http://PARTNER.Example.COM.:8143/x")
        self.assertEqual(host, "partner.example.com")
        self.assertEqual((scheme, port), ("http", 8143))


class TestNormaliseHost(unittest.TestCase):
    """Tested directly, because check_url cannot reach either behaviour.

    urlsplit already lower-cases an ASCII host, so a normaliser that dropped
    `.lower()` would still pass every URL in the table; and the Cyrillic host
    differs from "partner" whether or not IDNA runs, so the homograph row above
    passes for the wrong reason on its own. Both lines below are the ones that
    stop being true the moment someone puts an IDN or a shouted name in ALLOWED.
    """

    def test_lower_cases_ascii_that_idna_leaves_alone(self):
        """encodings.idna returns a pure-ASCII label untouched, case and all."""
        self.assertEqual(_normalise_host("PARTNER.Example.COM"),
                         "partner.example.com")

    def test_converts_a_u_label_to_its_a_label(self):
        """The wire form of the name. Refusing the homograph is not enough —
        this pins that the comparison happens in one alphabet, not two."""
        self.assertEqual(_normalise_host(f"p{CYRILLIC_A}rtner.example.com"),
                         "xn--prtner-3nf.example.com")

    def test_drops_exactly_one_root_dot(self):
        self.assertEqual(_normalise_host("partner.example.com."),
                         "partner.example.com")

    def test_empty_label_is_refused_as_malformed(self):
        with self.assertRaises(PolicyRefusal) as caught:
            _normalise_host("partner..com")
        self.assertEqual(caught.exception.code, "malformed")


class TestNoNetwork(unittest.TestCase):
    """F1: the gate is string and parse work, and stays that way."""

    def test_module_source_touches_no_resolver_or_socket(self):
        source = (HERE / "s07_gate.py").read_text()
        for token in ("import socket", "socket.", "getaddrinfo", "gethostby",
                      "urlopen", "urllib.request", "http.client"):
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
