#!/usr/bin/env python3
"""S10 — detect steering content in a fetched body, neutralise it, report it.

Detection first, defence second. S9 supplies the structural guarantee: a
nonce-delimited envelope the content cannot close. That stops the forgery; it
does not tell you a forgery was attempted. This slice answers the question the
envelope cannot -- *did anything in here try to steer us?* -- and RETURNS the
answer, because the lab's output contract has an `injection_detected` field and
a defence you cannot see fire is a defence you cannot audit.

    neutralise(body) -> (clean_text, findings)

`findings` is a list of JSON-serialisable records suitable for dropping
straight into the contract and the trace.

This module deliberately does NOT wrap anything in an envelope. Duplicating
S9's delimiter here would mean two places to get the nonce wrong.

--------------------------------------------------------------------------
Neutralisation strategy: mark the visible, delete the invisible.

  Classes 1-3 (role turns, chat templates, wrapper delimiters) are *visible
  text a reviewer needs to read*. Deleting them would hide the sentence that
  tried to steer the agent, which is the single most useful artefact of the
  whole run. They are replaced in place by a loud ASCII marker that names the
  rule and quotes the original in escaped form.

  Class 4 (zero-width, bidi, ANSI) is *invisible by construction* and carries
  no meaning for a human reader. It is deleted -- but not silently: every
  removal is reported with codepoint, count and line numbers. Marking it in
  place would be worse than useless: the fixture payload interleaves a
  zero-width space between every character, so per-character markers would
  bury the payload in 126 markers instead of revealing it. Deleting them
  *reveals* the hidden sentence to the human reviewing the trace, which is
  exactly the outcome the attacker was paying invisibility to avoid.

Idempotence is structural, not hopeful: every marker renders the matched text
with its FIRST character escaped (`<` -> `\\x3c`, `[` -> `\\x5b`), and every rule
below is anchored on that first literal character. A marker therefore cannot
re-match the rule that produced it. Class 4 leaves nothing behind to re-match.
"""
import bisect
import re
import unicodedata

CATEGORY_ROLE_TURN = "role_turn"
CATEGORY_CHAT_TEMPLATE = "chat_template"
CATEGORY_WRAPPER_FORGERY = "wrapper_forgery"
CATEGORY_INVISIBLE = "invisible_text"

ACTION_MARKED = "marked"
ACTION_REMOVED = "removed"

# Cap on unique line numbers carried per finding. The fixture payload spans one
# line; a pathological body should not be able to grow the contract without
# bound just by spreading zero-widths.
LINES_CAP = 20

# ---------------------------------------------------------------------------
# Class 4 -- invisible text.

_ZERO_WIDTH = {
    "​",  # ZERO WIDTH SPACE      -- the fixture's interleaving character
    "‌",  # ZERO WIDTH NON-JOINER
    "‍",  # ZERO WIDTH JOINER     -- carved out below when joining emoji
    "﻿",  # ZERO WIDTH NO-BREAK SPACE / BOM
}
# Bidi overrides reorder rendering without changing the codepoint order a model
# reads. The fixture uses U+202E to print a sentence backwards on screen.
_BIDI = {chr(cp) for cp in range(0x202A, 0x202F)} | {chr(cp) for cp in range(0x2066, 0x206A)}
_INVISIBLE = _ZERO_WIDTH | _BIDI

_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"           # CSI -- colour, cursor, erase-line
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC -- window title, hyperlink
    r"|\x1b[@-Z\\-_]"                      # two-character escapes
)

# ---------------------------------------------------------------------------
# Classes 1-3 -- visible steering markers.
#
# Each pattern is anchored on its first literal character; see the idempotence
# note in the module docstring.

# A forged turn needs a paragraph boundary to look like one, so the blank line
# is required rather than optional. This is the main false-positive control for
# this class: prose saying "the system: overloaded" mid-paragraph, or a single
# newline before "Human:", does not fire.
_ROLE_TURN = re.compile(
    r"\n[ \t\r]*\n[ \t\r]*(Human|Assistant|System)[ \t]*:",
    re.IGNORECASE,
)

# `<|...|>` forms have no innocent reading in any case, so they match
# case-insensitively. `[INST]` is matched case-SENSITIVELY and uppercase-only:
# ordinary technical documentation is full of `[install]`, `[inst]`, `[INFO]`
# and `[1]`, and only the exact uppercase token is a Llama chat delimiter.
_CHAT_TEMPLATE_CI = {"<|im_start|>": "im_start", "<|im_end|>": "im_end"}
_CHAT_TEMPLATE_CS = {"[INST]": "inst_open", "[/INST]": "inst_close"}
_CHAT_TEMPLATE_CI_RE = re.compile(
    "|".join(re.escape(k) for k in _CHAT_TEMPLATE_CI), re.IGNORECASE
)
_CHAT_TEMPLATE_CS_RE = re.compile("|".join(re.escape(k) for k in _CHAT_TEMPLATE_CS))

# In this lab the envelope delimiter carries a per-call random nonce, so a bare
# `<untrusted` in fetched content cannot be the wrapper and cannot be chance --
# it is someone guessing at the delimiter.
# Whitespace-tolerant and `\b`-terminated to match S9's own envelope check
# exactly: a variant that evades this scan but still reads as envelope markup to
# the wrapper would be a hole between two defences that are supposed to agree.
_WRAPPER = re.compile(r"<\s*/?\s*untrusted\b", re.IGNORECASE)

_NOTES = {
    CATEGORY_ROLE_TURN:
        "forges a conversation boundary so following text reads as a new turn",
    CATEGORY_CHAT_TEMPLATE:
        "chat-template delimiter; forges a role boundary at the tokeniser level",
    CATEGORY_WRAPPER_FORGERY:
        "envelope delimiter is nonce-bound, so a literal occurrence in fetched "
        "content is a forgery attempt, not a coincidence",
    CATEGORY_INVISIBLE:
        "hidden from a human reading the page or scrolling the trace; attacks "
        "the reviewer's own check, not the model",
}


def _render(text):
    """Quote `text` for a marker or a finding so it cannot act on any reader.

    Two jobs. (1) Invisible and control characters become visible escapes, so a
    finding pasted into a terminal trace cannot re-run the attack on the person
    reading it. (2) The FIRST character is always escaped, which is what makes
    markers non-re-matching and therefore neutralise() idempotent.
    """
    out = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if i == 0 or cp < 0x20 or cp == 0x7F or ch in _INVISIBLE or cp > 0x7E:
            out.append("\\u%04x" % cp if cp > 0xFF else "\\x%02x" % cp)
        elif ch == "\\":
            out.append("\\\\")  # keep the escape alphabet unambiguous
        else:
            out.append(ch)
    return "".join(out)


def _marker(rule, matched):
    return '[[NEUTRALISED %s "%s"]]' % (rule, _render(matched))


def _codepoint_rule(ch):
    name = unicodedata.name(ch, "U+%04X" % ord(ch))
    # Hyphens out too: rule ids are used as map keys and grep targets.
    slug = name.lower().replace(" ", "_").replace("-", "_")
    return "%s.%s" % (CATEGORY_INVISIBLE, slug)


def _is_pictographic(ch):
    """Approximate 'emoji-ish' by range -- no data tables, stdlib only."""
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF
            or cp in (0xFE0F, 0x2640, 0x2642))


class _Collector:
    """Aggregates occurrences into one record per (category, rule, match).

    One record per occurrence would mean 126 rows for the fixture payload. The
    count IS the signal there -- one stray zero-width is a copy-paste artefact,
    126 interleaved ones are steganography -- so occurrences are folded and the
    count kept.
    """

    def __init__(self, newlines):
        self._newlines = newlines
        self._records = {}

    def _line(self, offset):
        return bisect.bisect_right(self._newlines, offset - 1) + 1

    def add(self, category, rule, matched, start, end, action):
        key = (category, rule, _render(matched))
        rec = self._records.get(key)
        line = self._line(start)
        if rec is None:
            rec = {
                "category": category,
                "rule": rule,
                "match": _render(matched),
                "count": 0,
                "action": action,
                "first_offset": start,   # offset into the ORIGINAL body
                "first_line": line,
                "lines": [],
                "lines_truncated": False,
                "note": _NOTES[category],
            }
            self._records[key] = rec
        rec["count"] += 1
        if line not in rec["lines"]:
            if len(rec["lines"]) < LINES_CAP:
                rec["lines"].append(line)
            else:
                rec["lines_truncated"] = True
        _ = end  # span kept in the signature; only the start is reported

    def findings(self):
        out = sorted(self._records.values(), key=lambda r: r["first_offset"])
        for rec in out:
            rec["lines"].sort()
        return out


def _strip_invisible(body, collector):
    """Phase A: drop class 4, returning (clean, index_map).

    Runs BEFORE the visible-marker scan on purpose. A payload that writes
    "Hu<ZWSP>man:" evades a naive role-turn regex while still reading as
    "Human:" to a model that normalises. Stripping first closes that evasion,
    and `index_map` carries clean offsets back to original ones so findings
    still point at the real body.
    """
    ansi_spans = [m.span() for m in _ANSI.finditer(body)]
    for start, end in ansi_spans:
        collector.add(CATEGORY_INVISIBLE, "%s.ansi_escape" % CATEGORY_INVISIBLE,
                      body[start:end], start, end, ACTION_REMOVED)
    ansi_starts = [s for s, _ in ansi_spans]

    kept_chars = []
    index_map = []
    i = 0
    n = len(body)
    while i < n:
        # Skip whole ANSI sequences; their inner bytes are not content.
        pos = bisect.bisect_right(ansi_starts, i) - 1
        if pos >= 0 and ansi_spans[pos][0] <= i < ansi_spans[pos][1]:
            i = ansi_spans[pos][1]
            continue
        ch = body[i]
        if ch in _INVISIBLE:
            if ch == "﻿" and i == 0:
                # A leading BOM is decoding residue, not content. Dropping it
                # without a finding is the single most important false-positive
                # control here: otherwise every UTF-8-with-BOM page on the web
                # would set injection_detected.
                i += 1
                continue
            if (ch == "‍" and 0 < i < n - 1
                    and _is_pictographic(body[i - 1]) and _is_pictographic(body[i + 1])):
                # ZWJ between two pictographs is an emoji sequence, not a
                # payload. Removing it would corrupt the text it joins.
                kept_chars.append(ch)
                index_map.append(i)
                i += 1
                continue
            collector.add(CATEGORY_INVISIBLE, _codepoint_rule(ch), ch,
                          i, i + 1, ACTION_REMOVED)
            i += 1
            continue
        kept_chars.append(ch)
        index_map.append(i)
        i += 1
    return "".join(kept_chars), index_map


def _visible_matches(clean):
    """Phase B: locate classes 1-3 in the invisible-free text."""
    found = []
    for m in _ROLE_TURN.finditer(clean):
        rule = "%s.%s" % (CATEGORY_ROLE_TURN, m.group(1).lower())
        found.append((m.start(), m.end(), CATEGORY_ROLE_TURN, rule))
    for m in _CHAT_TEMPLATE_CI_RE.finditer(clean):
        rule = "%s.%s" % (CATEGORY_CHAT_TEMPLATE, _CHAT_TEMPLATE_CI[m.group(0).lower()])
        found.append((m.start(), m.end(), CATEGORY_CHAT_TEMPLATE, rule))
    for m in _CHAT_TEMPLATE_CS_RE.finditer(clean):
        rule = "%s.%s" % (CATEGORY_CHAT_TEMPLATE, _CHAT_TEMPLATE_CS[m.group(0)])
        found.append((m.start(), m.end(), CATEGORY_CHAT_TEMPLATE, rule))
    for m in _WRAPPER.finditer(clean):
        suffix = "close" if "/" in m.group(0) else "open"
        rule = "%s.untrusted_%s" % (CATEGORY_WRAPPER_FORGERY, suffix)
        found.append((m.start(), m.end(), CATEGORY_WRAPPER_FORGERY, rule))

    # Longest match wins at a shared start, then take non-overlapping spans, so
    # one region cannot be double-marked by two rules.
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    resolved = []
    cursor = -1
    for start, end, category, rule in found:
        if start < cursor:
            continue
        resolved.append((start, end, category, rule))
        cursor = end
    return resolved


def neutralise(body):
    """Neutralise steering content in `body`; return (clean_text, findings).

    `findings` is always returned -- an empty list means the body was clean,
    never that the scan was skipped. Feed `bool(findings)` to the contract's
    `injection_detected` field and the records themselves to the trace.
    """
    if body is None:
        return "", []
    if not isinstance(body, str):
        raise TypeError("neutralise() takes text, not %s" % type(body).__name__)

    newlines = [i for i, ch in enumerate(body) if ch == "\n"]
    collector = _Collector(newlines)

    clean, index_map = _strip_invisible(body, collector)

    pieces = []
    cursor = 0
    for start, end, category, rule in _visible_matches(clean):
        matched = clean[start:end]
        # Map back through the strip so the reported offset points at the real
        # body a reviewer will open, not at our intermediate copy.
        origin = index_map[start] if start < len(index_map) else len(body)
        collector.add(category, rule, matched, origin, origin + len(matched),
                      ACTION_MARKED)
        pieces.append(clean[cursor:start])
        pieces.append(_marker(rule, matched))
        cursor = end
    pieces.append(clean[cursor:])

    return "".join(pieces), collector.findings()


if __name__ == "__main__":  # pragma: no cover - convenience probe
    import json
    import pathlib
    fixture = (pathlib.Path(__file__).resolve().parents[2]
               / "fixtures" / "site-docs" / "sla" / "ingest.html")
    text, found = neutralise(fixture.read_text(encoding="utf-8"))
    print(json.dumps(found, indent=2))
