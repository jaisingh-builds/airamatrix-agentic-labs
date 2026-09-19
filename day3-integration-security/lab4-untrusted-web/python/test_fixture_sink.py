#!/usr/bin/env python3
"""Lab 4 S3 checks — the attacker's sink accepts everything and logs it.

The lab's headline claim is that the agent never reached the attacker, and the
evidence is this server's empty log. An empty log means nothing unless the
server would have recorded the request had it arrived. So these checks are all
about the sink being *willing*: any method, any path, any body, always 200,
always a record. Every refusal it could make is a way for a broken allow-list
to look like a working one.
"""
import datetime
import json
import pathlib
import sys
import unittest
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
LAB = HERE.parent
sys.path.insert(0, str(HERE))

from fixture_sites import sink


def _fetch(path, data=None, method=None):
    """(status, body). Timeout so a wedged sink fails the run instead of hanging it."""
    request = urllib.request.Request(f"http://127.0.0.1:{sink.PORT}{path}",
                                     data=data, method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


class TestSinkLogLocation(unittest.TestCase):
    def test_log_lives_in_the_lab_fixtures_dir(self):
        """Anywhere else and the gitignore rule that covers it does not apply."""
        self.assertEqual(sink.log_path().parent, LAB / "fixtures")
        self.assertEqual(sink.log_path().name, ".sink.log")


class TestSinkRecords(unittest.TestCase):
    """What lands in the log has to be evidence, not a tally."""

    def setUp(self):
        sink.serve_in_background()
        sink.truncate_log()     # per test: a line from the last test is a false failure

    def test_get_records_method_path_and_query(self):
        """The query string is where an exfiltrated value rides. A record that
        keeps the path but drops the query cannot show what was sent."""
        status, _ = _fetch("/collect?q=812&user=alice")
        self.assertEqual(status, 200)
        record, = sink.read_log()
        self.assertEqual(record["method"], "GET")
        self.assertEqual(record["path"], "/collect?q=812&user=alice")

    def test_post_body_is_captured(self):
        status, _ = _fetch("/drop", data=b"secret=max_queue_depth%3D812", method="POST")
        self.assertEqual(status, 200)
        record, = sink.read_log()
        self.assertEqual(record["method"], "POST")
        self.assertIn("812", record["body"])

    def test_user_agent_is_recorded(self):
        _fetch("/collect")
        record, = sink.read_log()
        self.assertTrue(record["user_agent"], "who called is part of the evidence")

    def test_timestamp_is_iso(self):
        """When it happened has to be comparable against the agent's trace."""
        _fetch("/collect")
        record, = sink.read_log()
        datetime.datetime.fromisoformat(record["ts"])      # raises if it is not ISO

    def test_one_line_per_request(self):
        _fetch("/collect?q=812")
        raw = sink.log_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads(raw[0]), sink.read_log()[0])


class TestSinkRefusesNothing(unittest.TestCase):
    """Every rejection the sink could make is a way for a broken allow-list to
    pass the lab. It must have none."""

    def setUp(self):
        sink.serve_in_background()
        sink.truncate_log()

    def test_unusual_methods_are_accepted_and_logged(self):
        for method in ("PUT", "DELETE", "PATCH", "FROB"):
            with self.subTest(method=method):
                sink.truncate_log()
                status, _ = _fetch("/anything", method=method)
                self.assertEqual(status, 200, f"{method} must not be refused")
                self.assertEqual(sink.read_log()[0]["method"], method)

    def test_head_is_accepted_with_no_body(self):
        status, body = _fetch("/beacon", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(sink.read_log()[0]["method"], "HEAD")

    def test_unrouted_path_is_not_a_404(self):
        """Nothing routes here. A 404 would read as an allow-list working."""
        status, _ = _fetch("/no/such/route/at/all")
        self.assertEqual(status, 200)
        self.assertEqual(len(sink.read_log()), 1)


class TestSinkLogLifecycle(unittest.TestCase):
    def setUp(self):
        sink.serve_in_background()
        sink.truncate_log()

    def test_requests_append_rather_than_overwrite(self):
        """Two hits must leave two records, in order — one overwriting the other
        would under-report the leak."""
        _fetch("/first?n=1")
        _fetch("/second?n=2")
        self.assertEqual([r["path"] for r in sink.read_log()],
                         ["/first?n=1", "/second?n=2"])

    def test_truncate_log_empties_it(self):
        _fetch("/collect?q=812")
        self.assertEqual(len(sink.read_log()), 1)
        sink.truncate_log()
        self.assertEqual(sink.read_log(), [])
        self.assertEqual(sink.log_path().read_text(encoding="utf-8"), "")

    def test_read_log_on_a_missing_file_is_empty_not_an_error(self):
        """"Never called" is the expected outcome; it must not look like a
        broken fixture."""
        sink.log_path().unlink(missing_ok=True)
        self.assertEqual(sink.read_log(), [])

    def test_serve_in_background_is_idempotent(self):
        first = sink.serve_in_background()
        second = sink.serve_in_background()
        self.assertIs(first, second, "a second call must not bind the port again")
        self.assertEqual(_fetch("/still-up")[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
