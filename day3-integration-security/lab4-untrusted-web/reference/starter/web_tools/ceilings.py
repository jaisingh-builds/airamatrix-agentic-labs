"""S11 — the two ceilings the allow-list does not provide.

An allow-list answers *where* a fetch may go. It never answers *how much* comes
back or *how often* the agent may ask. A control that answers one of three
questions has a hole in it, and in this lab the hole is load-bearing: the agent
follows links it scraped out of pages it already fetched, so one allow-listed
page can legitimately offer two hundred allow-listed URLs.

Two ceilings, deliberately separate because they fail for different reasons:

    bytes   read_bounded()  caps one body *during* the read
    fetches FetchBudget     caps how many requests a run may make at all

Neither is the other's backstop. A single 8 MB body blows the context with one
fetch; six well-behaved 2 KB bodies fetched four hundred times blows the wall
clock and the target's patience. The step cap bounds neither, because one turn
can request many fetches.
"""
import json

# ---------------------------------------------------------------- refusals

# Enumerated, and mirroring s07_gate.REFUSAL_CODES, so S12 can assemble the two
# namespaces and assert they stay disjoint instead of trusting that nobody
# reaches for "host" twice.
REFUSAL_CODES = frozenset({"byte_cap", "fetch_cap"})


class EgressRefusal(Exception):
    """A fetch stopped by a ceiling, carrying why in a machine-readable code.

    A run that died on volume and one that died on policy are different
    incidents. Without a code they are the same line in a trace, and the first
    question anyone asks of a refusal — "was this an attack or a fat page?" —
    has to be answered by reading prose.

    Codes here are `byte_cap` and `fetch_cap`; the allow-list slice owns
    `scheme`, `host`, `port` and `malformed`. They must not collide: a trace
    that groups by code is only as honest as the namespace behind it.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------- ceiling one: bytes

MAX_BODY_BYTES = 8 * 1024

# Read granularity. Small enough that the last read before the cap cannot
# overshoot it by much, large enough not to be a syscall per kilobyte.
_READ_CHUNK = 4096

# How many leading entries a structural summary shows. Five is enough to fix the
# record *shape* for the model; more just re-spends the budget the cap saved.
_SAMPLE_ITEMS = 5

# Long strings inside a sample are elided: one 40 KB description field would
# push the summary back over the cap it exists to respect.
_SCALAR_CHARS = 120

_DECODER = json.JSONDecoder()

_ACTIONABLE = (
    "Read cap reached: this is a structural sample of the leading bytes, not the "
    "document. Counts describe the sample only — the totals are null because this "
    "reader stopped at the cap and never saw the rest; do not infer them. "
    "Refetching this URL returns the same leading bytes. To get a specific record, "
    "ask for a narrower, filtered or paginated endpoint instead."
)


def read_bounded(response, limit=MAX_BODY_BYTES, on_over="summarise"):
    """Read at most `limit` bytes off `response`. Returns (text, truncated).

    The cap acts *during* the read. It deliberately does not consult
    Content-Length: a lying Content-Length costs an attacker nothing, so
    refusing on the header is a different and weaker control than counting bytes
    as they arrive. `/big-nocl.json` sends no Content-Length at all, and exists
    to make the header route impossible.

    Reading the whole body and slicing afterwards would satisfy every assertion
    about the returned string and none about the wire: the bytes have already
    been paid for by the time they are thrown away.

    on_over="refuse" raises instead of summarising, for callers whose next step
    needs the document exact (a parse, a hash, a signature) and for whom a
    faithful summary is still the wrong answer.
    """
    # TODO 5a — the byte ceiling. Every comment from this body is kept below.
    #
    # A limit of zero or less is a programming error, not a refusal: ValueError.
    #
    # One byte past the cap is what separates "exactly `limit` long" from
    # "longer than the cap". Without it a body that is exactly at the cap gets
    # reported as truncated, and the caller chases a summary of a whole
    # document.
    #
    # Read in _READ_CHUNK steps and stop at the budget. EOF is the only end
    # marker on the no-Content-Length bodies.
    #
    # "replace", not "strict": the cap lands on a byte boundary and will split a
    # multi-byte character. A UnicodeDecodeError here would turn a size ceiling
    # into a crash on any page with an em dash near 8 KB.
    #
    # Over the cap: on_over="refuse" raises EgressRefusal("byte_cap", ...);
    # otherwise hand the prefix to _summarise, which returns something the model
    # can still parse and which fits under this same cap.
    raise NotImplementedError(
        "TODO 5: read_bounded — read at most `limit` bytes off the response "
        "DURING the read, never trusting Content-Length, and return "
        "(text, truncated)")


def _summarise(prefix, limit):
    """Turn an over-cap prefix into something the model can still parse.

    Slicing an 8 MB JSON array at 8 KB is worse than returning nothing: the
    model cannot parse it, starts guessing at the rest, and burns the step cap
    guessing. Reference implementation of the idea:
    day1-foundations/lab1-bare-metal-loop/reference/agent.py::_summarise.

    It differs here in the one way that matters. That version had read the whole
    body, so it could report an exact item count. This one has 8 KB of an 8 MB
    document by design and cannot know the total — so every total is null and
    every count is labelled as covering the sample. Inventing "15621 rows" from
    a 15-row sample would be fabricating the fact the cap exists to avoid paying
    for.
    """
    # Shape is sniffed from the first byte, never from Content-Type: a host that
    # lies about its body size lies about its type for free, and a body labelled
    # application/json that is really 8 MB of HTML would otherwise be handed to
    # the JSON walker and come back as an empty sample rather than as text.
    body = prefix.lstrip()
    if body[:1] == "[":
        sample = _array_sample(body[1:])
        shape = "array"
    elif body[:1] == "{":
        sample = _object_sample(body[1:])
        shape = "object"
    else:
        # Not JSON. Text survives slicing, so the prefix stands; the marker is
        # what keeps the truncation visible in a transcript, where the caller
        # sees the string and not the boolean. The prefix gives up room for
        # it, so the cap means one thing on every path: nothing longer
        # than `limit` bytes comes back, summarised or sliced.
        marker = "\n\n[truncated at %d bytes - %s]" % (limit, _ACTIONABLE)
        # Budgeted in bytes, not characters: the marker carries an em dash,
        # and trimming by len() leaves the result two bytes over the cap.
        # "ignore" on the tail rather than "replace" for the same reason — a
        # replacement char costs three bytes and puts it back over.
        room = max(0, limit - len(marker.encode("utf-8")))
        return prefix.encode("utf-8")[:room].decode("utf-8", "ignore") + marker

    envelope = {
        "truncated": True,
        "byte_cap": limit,
        "bytes_sampled": limit,
        "total_bytes": None,
        "shape": shape,
        "sample": sample,
        "note": _ACTIONABLE,
    }
    return _fit(envelope, limit, shape)


def _fit(envelope, limit, shape):
    """Render the summary, dropping samples until it fits under the same cap.

    A summary that blows the ceiling it was produced to respect is a second copy
    of the bug. Items go first because the note and the counts are the parts a
    model cannot reconstruct.
    """
    while True:
        out = json.dumps(envelope, ensure_ascii=False)
        if len(out.encode("utf-8")) <= limit:
            return out
        if not _drop_one_item(envelope["sample"], shape):
            # Nothing left to shed: the header alone is over the cap, which means
            # the cap is smaller than a sentence. Return it anyway — a caller
            # that set a 50-byte cap gets valid JSON and a size surprise, not a
            # silent slice.
            return out


def _drop_one_item(sample, shape):
    if shape == "array" and sample["items"]:
        sample["items"].pop()
        return True
    for entry in sample.get("arrays", {}).values():
        if entry["items"]:
            entry["items"].pop()
            return True
    return False


def _array_sample(fragment):
    found, items = _leading_values(fragment)
    return {
        # Two numbers, and conflating them lies in both directions: `found` is
        # how many whole entries fit under the cap, `items` is how many are
        # shown. Neither is the array's length, which is why total_items is null.
        "complete_items_in_sample": found,
        "total_items": None,
        "items": items,
    }


def _object_sample(fragment):
    """Walk `"key": value` pairs off the front of a truncated object.

    Scalars carry the facts; arrays and nested objects are where the 8 MB went.
    The pair that straddles the cap is the interesting one — if it opened an
    array, its complete leading entries are still recoverable.
    """
    scalars, arrays, nested, keys = {}, {}, [], []
    idx, n = 0, len(fragment)

    while True:
        idx = _skip(fragment, idx, " \t\r\n,")
        if idx >= n or fragment[idx] != '"':
            break
        try:
            key, idx = _DECODER.raw_decode(fragment, idx)
        except ValueError:
            break            # the key itself straddles the cap
        idx = _skip(fragment, idx, " \t\r\n")
        if idx >= n or fragment[idx] != ":":
            break
        idx = _skip(fragment, idx + 1, " \t\r\n")
        if idx >= n:
            break
        keys.append(key)
        try:
            value, idx = _DECODER.raw_decode(fragment, idx)
        except ValueError:
            # The value straddles the cap. What is still recoverable depends on
            # what it opened with, and dropping the key outright would hide from
            # the model that the field exists at all.
            opener = fragment[idx]
            if opener == "[":
                arrays[key] = _array_sample(fragment[idx + 1:])
            elif opener == '"':
                head = _partial_string(fragment[idx + 1:])
                if head is None:
                    nested.append(key)
                else:
                    scalars[key] = head
            else:
                nested.append(key)
            break
        if isinstance(value, list):
            arrays[key] = {
                "complete_items_in_sample": len(value),
                "total_items": len(value),    # this array closed inside the cap,
                "items": [_elide(v) for v in value[:_SAMPLE_ITEMS]],  # so it is known
            }
        elif isinstance(value, dict):
            nested.append(key)
        else:
            scalars[key] = _elide(value)

    return {
        "keys_in_sample": keys,
        "scalars": scalars,
        "arrays": arrays,
        "nested_keys_omitted": nested,
    }


def _partial_string(fragment):
    """Recover the readable head of a string value the cap cut in half.

    The head is where the fact usually is: a 40 KB description field still
    answers "what is this record" in its first line. Closing the quote makes it
    parseable; shaving is for the tail, where the cap may have landed inside a
    \\uXXXX escape that no amount of quoting will fix.
    """
    head = fragment[: _SCALAR_CHARS * 2]
    for _ in range(8):
        # A trailing backslash escapes the quote being added and reopens the
        # string that was just closed.
        head = head.rstrip("\\")
        try:
            value, _ = _DECODER.raw_decode('"' + head + '"')
        except ValueError:
            head = head[:-1]
            continue
        return value[:_SCALAR_CHARS] + "...[elided]"
    return None


def _leading_values(fragment):
    """Peel complete JSON values off the front of an array body.

    Returns (how many whole values the prefix held, the first few of them).

    raw_decode is the stdlib's incremental door: it decodes one value and hands
    back where it stopped. The value straddling the cap raises, which is the
    signal to stop — that partial record is exactly what must not reach the
    model dressed as data.
    """
    shown, found, idx, n = [], 0, 0, len(fragment)
    while True:
        idx = _skip(fragment, idx, " \t\r\n,")
        if idx >= n:
            break
        try:
            value, idx = _DECODER.raw_decode(fragment, idx)
        except ValueError:
            break
        found += 1
        if len(shown) < _SAMPLE_ITEMS:
            shown.append(_elide(value))
    return found, shown


def _skip(text, idx, chars):
    while idx < len(text) and text[idx] in chars:
        idx += 1
    return idx


def _elide(value):
    """Shorten long strings in place, recursively, keeping the shape intact."""
    if isinstance(value, str) and len(value) > _SCALAR_CHARS:
        return value[:_SCALAR_CHARS] + "...[elided]"
    if isinstance(value, dict):
        return {k: _elide(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_elide(v) for v in value]
    return value


# ----------------------------------------------------------- ceiling two: fetches

MAX_FETCHES = 6


class FetchBudget:
    """How many requests one run may make, counted centrally.

    Central, and blind to the host. Per-host counters are the tempting design
    and they leak: the agent follows links out of fetched pages, so an
    allow-listed page that lists two hundred allow-listed URLs stays inside
    every per-host budget while making two hundred requests.
    """

    def __init__(self, max_fetches=MAX_FETCHES):
        self.max_fetches = max_fetches
        self.used = 0

    def reserve(self, url=""):
        """Claim one fetch *before* the request leaves. Raises when spent.

        Reserved, not recorded: charging after the response arrives means a
        request that hangs for the full timeout costs the budget nothing, and
        the ceiling is bypassed by anything slow.

        Failures are charged too. A fetch that 500s has already cost the target
        a connection and the run a step, and a budget that only counts successes
        turns a page which always errors into unlimited egress.
        """
        # TODO 5b — the fetch ceiling. Every comment from this body is kept
        # in the docstring above; what is left to you is the decision.
        #
        # Over budget raises EgressRefusal("fetch_cap", ...) with a message the
        # model can act on: how many of how many were used, the refused url when
        # there is one, and what to do instead. Under budget charges one and
        # returns the new count.
        raise NotImplementedError(
            "TODO 5: FetchBudget.reserve — claim one fetch before the request "
            "leaves, raising EgressRefusal('fetch_cap', ...) once the budget is "
            "spent")

    def remaining(self):
        return max(0, self.max_fetches - self.used)

    def reset(self):
        """Zero the count. One run's egress is not another run's allowance."""
        self.used = 0


# The process-wide budget the tool boundary spends from. Tests get isolation via
# reset_fetches(); a fresh FetchBudget() is there for callers that want their own.
FETCHES = FetchBudget()


def reserve_fetch(url=""):
    return FETCHES.reserve(url)


def fetches_used():
    return FETCHES.used


def reset_fetches():
    FETCHES.reset()
