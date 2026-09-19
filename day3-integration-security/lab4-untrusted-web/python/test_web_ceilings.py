#!/usr/bin/env python3
"""Lab 4 checks for the two ceilings (S11).

The load-bearing test is TestCapActsOnTheWire. Everything else can be satisfied
by an implementation that downloads 8 MB and slices it: the returned string is
the same either way, so measuring the string proves only that the client threw
bytes away *after* paying for them. The fixture origins count body bytes written
to the socket, and that counter is the only evidence that the cap acted during
the read.

Fixtures are addressed as 127.0.0.1:PORT. Hostname resolution belongs to
fixture_sites.dns; depending on it here would make this suite slow and
network-sensitive for no gain.
"""
import io
import json
import pathlib
import sys
import time
import unittest
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE))

import web_tools.ceilings as ceilings                                  # noqa: E402
from fixture_sites import origins                                # noqa: E402

# Returns only the origins this process actually started; a port already open is
# reused and left out. That distinction is the gate's whole footing — see
# TestCapActsOnTheWire.setUp.
_OWNED = origins.serve_in_background()
WE_SERVE_STATUS = "status" in _OWNED

STATUS = origins.base_url("status")
SMALL_URL = f"{STATUS}/ingest/status.json"

# The server writes the big bodies in 64 KB chunks and the kernel buffers a few
# before the write blocks, so the smallest observable figure is chunk-granular,
# not cap-granular: an 8 KB cap still costs a whole chunk.
SERVER_CHUNK_BYTES = 64 * 1024

# Both bounds are SETTLED figures — sampled only after settled_bytes() has seen
# the counter start and then stop. An unsettled sample is a different and much
# smaller number, and calibrating against one is how this suite would get a
# ceiling that looks generous and is actually racing.
#
# Settled range for a capped read: 65,536 - 329,787 here, 1,187,982 reported on
# the committed fixtures. A naive read-everything-then-slice implementation
# lands on the body size exactly, every time, because it reads to EOF. Half the
# body therefore separates the two with ~3.5x headroom above the worst
# legitimate figure and 2x below the only figure the broken implementation can
# produce, and it does not depend on anyone's kernel buffer sizes.
WIRE_CEILING = origins.BIG_JSON_BYTES // 2

# The floor. `> 0` is not enough: zero is a reading you genuinely get when the
# counter is sampled before the server thread has been scheduled, and zero
# satisfies every upper bound here — so the gate would pass having measured
# nothing at all. The server cannot write less than one chunk of body, so one
# chunk is the smallest honest evidence that the measurement happened.
WIRE_FLOOR = SERVER_CHUNK_BYTES

COUNTER_NOT_OURS = (
    "the fixture byte counter is in another process. fixture_sites.origins reuses "
    "a port that is already open, so when a sibling test run owns 127.0.0.1:8141 "
    "the counter that matters lives over there and this gate can only ever read "
    "zero. This is not a regression in read_bounded: stop the other run, or run "
    "this suite with the fixture ports free. Failing rather than skipping is "
    "deliberate — a gate that skips is a gate that quietly stops running."
)


def settled_bytes(quiet=0.6, timeout=10.0):
    """Wait for the server thread to start writing, then to stop, then sample.

    Two waits, and dropping either one breaks the gate in a different direction.

    Waiting only for the counter to stop moving returns zero when the server
    thread has not been scheduled yet: the first two samples agree at zero, the
    helper calls that "settled", and zero passes every upper bound in this file.
    A gate that passes because the measurement never happened is worse than no
    gate, because it reports success.

    Waiting only for it to start samples mid-stream and understates the figure,
    which would make an uncapped read look capped.

    `quiet` is a sustained no-movement window rather than two equal samples: the
    server blocks in wfile.write while the client's receive buffer drains, so
    the counter genuinely plateaus mid-stream and a two-sample test mistakes
    that pause for the end of the body.

    origins exposes no completion signal — only bytes_written() and
    reset_byte_counter() — so polling is the most deterministic wait available.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if origins.bytes_written() > 0:
            break                     # the first write has landed
        time.sleep(0.02)

    # Deliberately not asserting the floor here: a genuine sub-chunk write
    # should reach the test and fail its assertion loudly, not time out inside
    # a helper where the number never gets reported.
    last, last_moved = origins.bytes_written(), time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.05)
        current = origins.bytes_written()
        if current != last:
            last, last_moved = current, time.monotonic()
        elif time.monotonic() - last_moved >= quiet:
            break
    return origins.bytes_written()


def fetch_bounded(url, **kwargs):
    """Read one URL under the cap, closing the response so the server unblocks."""
    response = urllib.request.urlopen(url, timeout=30)
    try:
        return ceilings.read_bounded(response, **kwargs)
    finally:
        response.close()


class _LyingResponse:
    """A body whose declared size has nothing to do with its real one.

    Not a mock of http.client: read_bounded is only ever handed something with
    .read(n), and giving it .headers is how a header-trusting implementation
    gets the chance to take the bait.
    """

    def __init__(self, body, declared):
        self._stream = io.BytesIO(body)
        self.headers = {"Content-Length": str(declared)}

    def read(self, amount):
        return self._stream.read(amount)


# --------------------------------------------------------------- the gate

class TestCapActsOnTheWire(unittest.TestCase):
    """The only test a read cap actually depends on."""

    def setUp(self):
        # Checked once, here, rather than left to four separate "0 not greater
        # than 0" failures that each look like a broken cap.
        self.assertTrue(WE_SERVE_STATUS, COUNTER_NOT_OURS)
        origins.reset_byte_counter()
        self.assertEqual(origins.bytes_written(), 0)

    def assert_wire_cost_is_capped(self, written, label):
        """Both bounds, because each catches a different way to be wrong."""
        self.assertGreaterEqual(
            written, WIRE_FLOOR,
            "%s: the counter settled at %s bytes, under one %s-byte server chunk. "
            "The server cannot write less than a chunk of body, so this is not a "
            "capped read — it is a measurement that did not happen, and it proves "
            "nothing either way about read_bounded."
            % (label, format(written, ","), format(WIRE_FLOOR, ",")))
        self.assertLess(
            written, WIRE_CEILING,
            "%s: %s bytes crossed the wire off an %s-byte body. Either the cap is "
            "not acting during the read — reading to EOF lands on the body size "
            "exactly — or another process pulled a body through this origin while "
            "the counter was running. The counter is global to the origin, so a "
            "concurrent lab4 run inflates it; rule that out before calling this a "
            "regression."
            % (label, format(written, ","), format(origins.BIG_JSON_BYTES, ",")))

    def test_undeclared_length_body_never_crosses_the_wire(self):
        """/big-nocl.json sends no Content-Length, so the header route is shut.

        A pre-flight refusal on a declared size is a different and weaker
        control: a lying Content-Length costs an attacker nothing. This endpoint
        exists so the cap has nothing to read but the bytes themselves.
        """
        text, truncated = fetch_bounded(f"{STATUS}/big-nocl.json")

        written = settled_bytes()
        self.assertTrue(truncated)
        self.assert_wire_cost_is_capped(written, "/big-nocl.json")
        self.assertLessEqual(len(text.encode("utf-8")), ceilings.MAX_BODY_BYTES)

    def test_declared_length_body_bounds_identically(self):
        """Same body, one header apart. The header must not change behaviour.

        If this figure and the undeclared one diverge, something is reading
        Content-Length — which means the control works only against servers
        honest enough not to need capping.
        """
        _, truncated = fetch_bounded(f"{STATUS}/big.json")

        written = settled_bytes()
        self.assertTrue(truncated)
        self.assert_wire_cost_is_capped(written, "/big.json (declared length)")

    def test_reading_the_whole_body_would_fail_this_gate(self):
        """The control the gate is calibrated against.

        Without it the ceiling above is just a number nobody has shown can be
        exceeded. Reading the body in full must push the counter to the body's
        size, or the counter is not measuring what the other two tests claim.
        """
        with urllib.request.urlopen(f"{STATUS}/big-nocl.json", timeout=30) as response:
            whole = response.read()

        written = settled_bytes()
        self.assertEqual(len(whole), origins.BIG_JSON_BYTES)
        self.assertGreaterEqual(
            written, origins.BIG_JSON_BYTES,
            "reading to EOF must put the whole body on the wire, or the counter "
            "is not measuring what the two tests above rely on it measuring")
        self.assertGreater(written, WIRE_CEILING, "the ceiling does not discriminate")

    def test_byte_counter_resets_between_reads(self):
        """One stream's spend is not another's evidence."""
        fetch_bounded(f"{STATUS}/big-nocl.txt")
        self.assertGreaterEqual(settled_bytes(), WIRE_FLOOR,
                                "nothing was measured, so the reset below proves nothing")

        origins.reset_byte_counter()
        self.assertEqual(origins.bytes_written(), 0)


# ------------------------------------------------------- ceiling one: bytes

class TestOversizedJsonStaysParseable(unittest.TestCase):
    """Slicing JSON at a byte count is worse than truncating nothing: the model
    cannot parse it, starts guessing, and burns the step cap guessing."""

    def test_summary_of_the_big_body_parses(self):
        text, truncated = fetch_bounded(f"{STATUS}/big-nocl.json")
        self.assertTrue(truncated)

        doc = json.loads(text)          # a byte slice of this body raises here
        self.assertTrue(doc["truncated"])
        self.assertEqual(doc["shape"], "object")
        self.assertEqual(doc["byte_cap"], ceilings.MAX_BODY_BYTES)

    def test_summary_is_structural_not_a_byte_slice(self):
        doc = json.loads(fetch_bounded(f"{STATUS}/big-nocl.json")[0])
        rows = doc["sample"]["arrays"]["rows"]

        # Whole records, recovered from the prefix — not a string ending mid-row.
        self.assertGreaterEqual(rows["complete_items_in_sample"], 10)
        self.assertEqual(len(rows["items"]), ceilings._SAMPLE_ITEMS)
        self.assertEqual(sorted(rows["items"][0]), ["i", "pad"])
        self.assertEqual(rows["items"][0]["i"], "0000000")

        # Scalars carry the facts; this one is the only key that is not the 8 MB.
        self.assertIn("note", doc["sample"]["scalars"])
        self.assertEqual(doc["sample"]["keys_in_sample"], ["note", "rows"])

    def test_totals_are_null_never_guessed(self):
        """The count the summary cannot honestly report.

        The reference summariser in lab 1 had read the whole body and could give
        an exact item count. This one has 8 KB of 8 MB by construction. Reporting
        a total here would be inventing the fact the cap exists to avoid paying
        for, and a model that trusts it plans against a number nobody measured.
        """
        doc = json.loads(fetch_bounded(f"{STATUS}/big-nocl.json")[0])
        rows = doc["sample"]["arrays"]["rows"]

        self.assertIsNone(doc["total_bytes"])
        self.assertIsNone(rows["total_items"])
        self.assertLess(rows["complete_items_in_sample"], 15621)   # the real row count

    def test_summary_fits_under_the_cap_it_enforces(self):
        """A summary over the ceiling is a second copy of the bug."""
        text, _ = fetch_bounded(f"{STATUS}/big-nocl.json")
        self.assertLessEqual(len(text.encode("utf-8")), ceilings.MAX_BODY_BYTES)

    def test_note_tells_the_model_what_to_do_next(self):
        doc = json.loads(fetch_bounded(f"{STATUS}/big-nocl.json")[0])
        self.assertIn("paginated", doc["note"])
        self.assertIn("do not infer", doc["note"].lower())


class TestSummariseShapes(unittest.TestCase):
    """Offline. No origin needed to prove the summariser handles a shape."""

    def _summary(self, payload, **kwargs):
        text, truncated = ceilings.read_bounded(io.BytesIO(payload), **kwargs)
        self.assertTrue(truncated)
        return json.loads(text)

    def test_bare_array_reports_what_it_could_peel(self):
        body = ("[" + ",".join(json.dumps({"n": i}) for i in range(4000)) + "]").encode()
        doc = self._summary(body)

        self.assertEqual(doc["shape"], "array")
        self.assertEqual(len(doc["sample"]["items"]), ceilings._SAMPLE_ITEMS)
        self.assertGreater(doc["sample"]["complete_items_in_sample"], 5)
        self.assertIsNone(doc["sample"]["total_items"])
        self.assertEqual(doc["sample"]["items"][0], {"n": 0})

    def test_array_that_closes_inside_the_cap_knows_its_total(self):
        """total_items is null because the array ran past the cap, not on principle.

        An array that finished inside the prefix was fully counted, and saying
        "unknown" there would be as dishonest as guessing in the other direction.
        """
        body = json.dumps({"ids": list(range(8)), "blob": "w" * 30000}).encode()
        doc = self._summary(body)
        ids = doc["sample"]["arrays"]["ids"]

        self.assertEqual(ids["total_items"], 8)
        self.assertEqual(ids["complete_items_in_sample"], 8)
        self.assertEqual(ids["items"], [0, 1, 2, 3, 4])

    def test_partial_trailing_record_is_dropped_not_shown(self):
        """The record straddling the cap is the one that must not reach the model.

        Half a record dressed as data is what makes a model confident about a
        field it never saw.
        """
        rows = [{"id": i, "pad": "p" * 60} for i in range(400)]
        doc = self._summary(json.dumps({"rows": rows}).encode())
        items = doc["sample"]["arrays"]["rows"]["items"]

        self.assertEqual(len(items), ceilings._SAMPLE_ITEMS)
        for index, item in enumerate(items):
            self.assertEqual(sorted(item), ["id", "pad"])
            self.assertEqual(item, {"id": index, "pad": "p" * 60})

    def test_long_scalars_are_elided_not_carried(self):
        """One 40 KB description would otherwise re-spend the budget the cap saved."""
        body = json.dumps({"region": "ap-south-1", "description": "d" * 40000}).encode()
        doc = self._summary(body)
        scalars = doc["sample"]["scalars"]

        self.assertEqual(scalars["region"], "ap-south-1")
        self.assertTrue(scalars["description"].endswith("...[elided]"))
        self.assertLess(len(scalars["description"]), 200)

    def test_a_string_value_cut_in_half_keeps_its_readable_head(self):
        """Dropping the key would hide from the model that the field exists.

        The head is where the fact usually is, and this is the first value in
        the body, so there is nothing else in the sample to fall back on.
        """
        body = json.dumps({"summary": "queue depth is 812 and rising. " + "z" * 40000}).encode()
        doc = self._summary(body)

        self.assertIn("queue depth is 812", doc["sample"]["scalars"]["summary"])
        self.assertTrue(doc["sample"]["scalars"]["summary"].endswith("...[elided]"))

    def test_a_string_cut_inside_an_escape_does_not_break_the_summary(self):
        """The cap can land between the backslash and the rest of a \\uXXXX."""
        for pad in range(16):
            body = json.dumps({"s": "\u00e9" * 4000 + "x" * pad}).encode()
            doc = self._summary(body)
            self.assertEqual(doc["shape"], "object")   # parses whatever the cut was

    def test_nested_objects_are_named_not_dumped(self):
        body = json.dumps({"tier": "ingest",
                           "detail": {"k": "v" * 40000},
                           "trailing": 1}).encode()
        doc = self._summary(body)

        self.assertEqual(doc["sample"]["scalars"]["tier"], "ingest")
        self.assertIn("detail", doc["sample"]["nested_keys_omitted"])

    def test_non_json_body_is_sliced_with_a_visible_marker(self):
        """Text survives slicing; the marker is what survives a transcript.

        A caller reading the string and not the boolean would otherwise see a
        page that simply stops.
        """
        text, truncated = ceilings.read_bounded(io.BytesIO(b"filler line\n" * 5000))

        self.assertTrue(truncated)
        self.assertTrue(text.startswith("filler line"))
        self.assertIn("truncated at 8192 bytes", text)
        self.assertLessEqual(len(text.encode("utf-8")), ceilings.MAX_BODY_BYTES)

    def test_multibyte_text_still_fits_the_byte_cap(self):
        """The cap counts bytes; len() counts characters. Trimming by the wrong
        one puts the marker back over the ceiling it was trimmed to respect."""
        text, _ = ceilings.read_bounded(io.BytesIO(("é" * 40000).encode("utf-8")))
        self.assertLessEqual(len(text.encode("utf-8")), ceilings.MAX_BODY_BYTES)

    def test_split_multibyte_character_does_not_raise(self):
        """The cap lands on a byte boundary and will cut a character in half.

        Strict decoding here would turn a size ceiling into a crash on any page
        with an em dash near 8 KB.
        """
        text, truncated = ceilings.read_bounded(io.BytesIO("—".encode() * 9000))
        self.assertTrue(truncated)
        self.assertIsInstance(text, str)

    def test_summary_shrinks_to_fit_a_small_cap(self):
        body = ("[" + ",".join(json.dumps({"n": i, "p": "z" * 300}) for i in range(80)) + "]").encode()
        text, _ = ceilings.read_bounded(io.BytesIO(body), limit=2048)

        json.loads(text)                                  # still valid
        self.assertLessEqual(len(text.encode("utf-8")), 2048)


class TestSmallBodiesPassThrough(unittest.TestCase):
    def test_under_cap_body_is_returned_unmodified(self):
        raw = (pathlib.Path(origins.ROOT) / "site-status" / "ingest" / "status.json").read_text()
        text, truncated = fetch_bounded(SMALL_URL)

        self.assertFalse(truncated)
        self.assertEqual(text, raw)         # byte for byte: no marker, no envelope
        self.assertEqual(json.loads(text)["queue_depth"], 812)

    def test_body_exactly_at_the_cap_is_not_truncated(self):
        """The off-by-one that sends a caller chasing a summary of a whole body."""
        text, truncated = ceilings.read_bounded(io.BytesIO(b"y" * ceilings.MAX_BODY_BYTES))

        self.assertFalse(truncated)
        self.assertEqual(len(text), ceilings.MAX_BODY_BYTES)

    def test_empty_body(self):
        self.assertEqual(ceilings.read_bounded(io.BytesIO(b"")), ("", False))


class TestContentLengthIsNotConsulted(unittest.TestCase):
    """A declared size is a claim by the server being defended against."""

    def test_a_small_lie_does_not_shrink_a_large_body(self):
        response = _LyingResponse(b"z" * 60000, declared=12)
        text, truncated = ceilings.read_bounded(response)

        self.assertTrue(truncated, "believed a Content-Length of 12 over 60 KB of body")
        self.assertLessEqual(len(text.encode("utf-8")), ceilings.MAX_BODY_BYTES)

    def test_a_large_lie_does_not_truncate_a_small_body(self):
        response = _LyingResponse(b"small body", declared=9_000_000)
        self.assertEqual(ceilings.read_bounded(response), ("small body", False))


class TestByteCapRefusal(unittest.TestCase):
    """Truncating and refusing are both correct, for different callers.

    A page being read for context degrades fine. A body whose next step is a
    parse, a hash or a signature check must refuse rather than hand back a
    faithful summary that is not the document.
    """

    def test_refuse_mode_raises_with_the_byte_cap_code(self):
        with self.assertRaises(ceilings.EgressRefusal) as caught:
            ceilings.read_bounded(io.BytesIO(b"z" * 90000), on_over="refuse")

        self.assertEqual(caught.exception.code, "byte_cap")
        self.assertIn("8192", str(caught.exception))

    def test_refuse_mode_leaves_under_cap_bodies_alone(self):
        self.assertEqual(
            ceilings.read_bounded(io.BytesIO(b"fits"), on_over="refuse"), ("fits", False))

    def test_a_nonsense_limit_is_a_programming_error_not_a_refusal(self):
        with self.assertRaises(ValueError):
            ceilings.read_bounded(io.BytesIO(b"x"), limit=0)


# ----------------------------------------------------- ceiling two: fetches

class TestFetchBudget(unittest.TestCase):
    def setUp(self):
        ceilings.reset_fetches()

    def test_sixth_fetch_succeeds_and_seventh_is_refused(self):
        for expected in range(1, ceilings.MAX_FETCHES + 1):
            self.assertEqual(ceilings.reserve_fetch("http://127.0.0.1:8141/"), expected)

        self.assertEqual(ceilings.fetches_used(), 6)
        with self.assertRaises(ceilings.EgressRefusal) as caught:
            ceilings.reserve_fetch("http://127.0.0.1:8141/one-more")

        self.assertEqual(caught.exception.code, "fetch_cap")
        self.assertEqual(ceilings.fetches_used(), 6, "a refused fetch was still charged")

    def test_the_cap_is_blind_to_the_host(self):
        """An allow-list answers where, never how often.

        Per-host counters are the tempting design and they leak: the agent
        follows links out of pages it fetched, so one allow-listed page listing
        two hundred allow-listed URLs stays inside every per-host budget.
        """
        hosts = ["http://127.0.0.1:814%d/p" % (n % 4) for n in range(ceilings.MAX_FETCHES)]
        for url in hosts:
            ceilings.reserve_fetch(url)

        with self.assertRaises(ceilings.EgressRefusal) as caught:
            ceilings.reserve_fetch("http://127.0.0.1:8141/first-time-for-this-path")
        self.assertEqual(caught.exception.code, "fetch_cap")

    def test_refusal_message_is_something_a_model_can_act_on(self):
        for _ in range(ceilings.MAX_FETCHES):
            ceilings.reserve_fetch()
        with self.assertRaises(ceilings.EgressRefusal) as caught:
            ceilings.reserve_fetch("http://127.0.0.1:8141/late")

        message = str(caught.exception)
        self.assertIn("6 of 6", message)              # what was spent
        self.assertIn("127.0.0.1:8141/late", message)  # which request was dropped
        self.assertIn("Answer from what you already have", message)   # what to do now

    def test_reset_restores_the_whole_budget(self):
        for _ in range(ceilings.MAX_FETCHES):
            ceilings.reserve_fetch()
        self.assertEqual(ceilings.FETCHES.remaining(), 0)

        ceilings.reset_fetches()

        self.assertEqual(ceilings.fetches_used(), 0)
        self.assertEqual(ceilings.FETCHES.remaining(), ceilings.MAX_FETCHES)
        self.assertEqual(ceilings.reserve_fetch(), 1)

    def test_instances_do_not_share_a_count(self):
        """One run's egress is not another's allowance."""
        mine = ceilings.FetchBudget(max_fetches=2)
        mine.reserve()
        mine.reserve()

        with self.assertRaises(ceilings.EgressRefusal):
            mine.reserve()
        self.assertEqual(ceilings.fetches_used(), 0, "a private budget spent the shared one")

    def test_reserving_charges_before_the_request_leaves(self):
        """Charged on attempt, not on success.

        A budget that only counts responses is bypassed by anything slow, and a
        page that always errors becomes unlimited egress via the retry.
        """
        budget = ceilings.FetchBudget(max_fetches=1)
        budget.reserve("http://127.0.0.1:8141/will-fail")
        self.assertEqual(budget.used, 1)
        self.assertEqual(budget.remaining(), 0)


class TestRefusalCodes(unittest.TestCase):
    def test_volume_and_policy_are_different_incidents(self):
        """A trace that groups by code is only as honest as its namespace.

        The allow-list slice owns scheme/host/port/malformed. Colliding with one
        of those would merge "the page was 8 MB" into "the agent tried to reach
        the sink", and the first question anyone asks of a refusal is which of
        those happened.
        """
        codes = set()
        try:
            ceilings.read_bounded(io.BytesIO(b"z" * 90000), on_over="refuse")
        except ceilings.EgressRefusal as refusal:
            codes.add(refusal.code)

        budget = ceilings.FetchBudget(max_fetches=0)
        try:
            budget.reserve()
        except ceilings.EgressRefusal as refusal:
            codes.add(refusal.code)

        self.assertEqual(codes, set(ceilings.REFUSAL_CODES))
        self.assertEqual(codes, {"byte_cap", "fetch_cap"})

        # Read from the allow-list slice's own export, not a copy of it, so the
        # guarantee survives either family adding a code later. The fallback this
        # replaced was written while S7 was still landing; now that it is in the
        # tree, a fallback could only hide a broken import (it did: an import
        # rewrite at S12 left a stale reference that the except clause was one
        # exception type away from swallowing).
        from web_tools.gate import REFUSAL_CODES as gate_codes
        self.assertFalse(codes & set(gate_codes),
                         "a ceiling code shadows an allow-list code")

    def test_a_refusal_is_catchable_as_an_exception(self):
        self.assertTrue(issubclass(ceilings.EgressRefusal, Exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
