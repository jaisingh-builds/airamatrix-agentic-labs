#!/usr/bin/env python3
"""Lab 4 fixture DNS — offline, no network, no spend.

The patch these tests exercise is global to the interpreter, so every test
restores socket.getaddrinfo in tearDown. A leaked patch does not fail here; it
fails in whatever test file runs next, which is a much worse afternoon.

    python3 test_fixture_dns.py
"""
import pathlib
import socket
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fixture_sites import dns  # noqa: E402

NAMES = ("status.airamatrix.local", "docs.airamatrix.local",
         "partner.example.com", "evil.example.net")


class FixtureDNS(unittest.TestCase):

    def setUp(self):
        # Captured before any install(), so tearDown can undo a test that left
        # a spy or a half-installed patch behind.
        self._real_getaddrinfo = socket.getaddrinfo
        self._real_hosts = dict(dns.HOSTS)

    def tearDown(self):
        dns.uninstall()
        socket.getaddrinfo = self._real_getaddrinfo
        dns.HOSTS.clear()
        dns.HOSTS.update(self._real_hosts)          # the mutation test edits it in place

    def addresses(self, host, port=8137):
        return {info[4][0] for info in socket.getaddrinfo(host, port)}

    def test_every_fixture_name_resolves_to_loopback(self):
        dns.install()
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.addresses(name), {"127.0.0.1"})

    def test_unknown_host_is_delegated_to_whatever_was_there(self):
        """Fall-through proved without a network: install() over a spy."""
        seen = []

        def spy(host, port, *args, **kwargs):
            seen.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", port))]

        socket.getaddrinfo = spy
        dns.install()
        result = socket.getaddrinfo("unlisted.example.org", 443)
        self.assertEqual(seen, ["unlisted.example.org"])
        self.assertEqual(result[0][4][0], "203.0.113.9")

    def test_real_resolver_still_works_for_other_names(self):
        """localhost resolves from the hosts file, so this needs no network."""
        dns.install()
        self.assertTrue(socket.getaddrinfo("localhost", 80))

    def test_uninstall_restores_the_original_object(self):
        original = socket.getaddrinfo
        dns.install()
        self.assertIsNot(socket.getaddrinfo, original)
        dns.uninstall()
        # `is`, not ==: an equivalent wrapper that forwards correctly would pass
        # equality and still leave the process permanently patched.
        self.assertIs(socket.getaddrinfo, original)

    def test_install_twice_then_uninstall_once_fully_restores(self):
        """The trap: a naive install() wraps its own wrapper and uninstall()
        peels one layer, leaving the patch live for every later test."""
        original = socket.getaddrinfo
        dns.install()
        patched = socket.getaddrinfo
        dns.install()
        self.assertIs(socket.getaddrinfo, patched, "second install() nested a second wrapper")
        dns.uninstall()
        self.assertIs(socket.getaddrinfo, original)

    def test_uninstall_without_install_is_a_no_op(self):
        original = socket.getaddrinfo
        dns.uninstall()
        self.assertIs(socket.getaddrinfo, original)

    def test_hosts_mutation_is_seen_by_the_next_lookup(self):
        """The rebinding extension depends on this: same name, second answer."""
        dns.install()
        self.assertEqual(self.addresses("status.airamatrix.local"), {"127.0.0.1"})
        dns.HOSTS["status.airamatrix.local"] = "127.0.0.2"
        self.assertEqual(self.addresses("status.airamatrix.local"), {"127.0.0.2"})

    def test_hosts_is_a_mutable_dict_of_the_four_names(self):
        self.assertIsInstance(dns.HOSTS, dict)
        self.assertEqual(set(dns.HOSTS), set(NAMES))

    def test_no_host_maps_to_an_address_carrying_a_port(self):
        """Names resolve to addresses; ports belong to the URL. Keeping them
        apart is what lets the allow-list check (scheme, host, port) as three."""
        for name, address in dns.HOSTS.items():
            with self.subTest(name=name):
                self.assertNotIn(":", address)


if __name__ == "__main__":
    unittest.main(verbosity=2)
