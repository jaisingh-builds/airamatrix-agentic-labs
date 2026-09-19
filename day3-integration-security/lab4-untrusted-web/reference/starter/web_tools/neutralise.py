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
    # TODO 4a — strip class 4. Every comment from this body is kept below.
    #
    # _ANSI spans first: their inner bytes are not content, so skip the whole
    # sequence rather than its characters one at a time.
    #
    # A leading BOM is decoding residue, not content. Dropping it without a
    # finding is the single most important false-positive control here:
    # otherwise every UTF-8-with-BOM page on the web would set
    # injection_detected.
    #
    # ZWJ between two pictographs is an emoji sequence, not a payload. Removing
    # it would corrupt the text it joins.
    #
    # Everything else in _INVISIBLE is removed and REPORTED via
    # collector.add(..., ACTION_REMOVED) with _codepoint_rule(ch) as the rule.
    #
    # index_map carries each kept character's offset in the ORIGINAL body, so
    # findings from phase B still point at the real bytes a reviewer will open.
    raise NotImplementedError(
        "TODO 4: _strip_invisible — remove zero-width, bidi and ANSI runs, "
        "reporting every removal, and return (clean_text, index_map)")


def _visible_matches(clean):
    """Phase B: locate classes 1-3 in the invisible-free text."""
    # TODO 4b — locate classes 1-3. Every comment from this body is kept below.
    #
    # Scan _ROLE_TURN, _CHAT_TEMPLATE_CI_RE, _CHAT_TEMPLATE_CS_RE and _WRAPPER,
    # naming each rule "<category>.<specific>" so a finding is greppable.
    #
    # Longest match wins at a shared start, then take non-overlapping spans, so
    # one region cannot be double-marked by two rules.
    raise NotImplementedError(
        "TODO 4: _visible_matches — return non-overlapping (start, end, "
        "category, rule) spans for the role-turn, chat-template and "
        "wrapper-forgery markers in `clean`")


def neutralise(body):
    """Neutralise steering content in `body`; return (clean_text, findings).

    `findings` is always returned -- an empty list means the body was clean,
    never that the scan was skipped. Feed `bool(findings)` to the contract's
    `injection_detected` field and the records themselves to the trace.
    """
    # TODO 4c — the two phases. Every comment from this body is kept below.
    #
    # None is an empty body, not an error; a non-str body is a programming
    # error and raises TypeError.
    #
    # Phase A (_strip_invisible) runs BEFORE the visible scan on purpose. A
    # payload that writes "Hu<ZWSP>man:" evades a naive role-turn regex while
    # still reading as "Human:" to a model that normalises.
    #
    # Map each phase-B offset back through index_map so the reported offset
    # points at the real body a reviewer will open, not at our intermediate
    # copy.
    #
    # Visible matches are replaced in place by _marker(rule, matched) — marked,
    # never deleted: the sentence that tried to steer the agent is the single
    # most useful artefact of the whole run.
    raise NotImplementedError(
        "TODO 4: neutralise — strip the invisible, mark the visible, and return "
        "(clean_text, findings) with one record per (category, rule, match)")


if __name__ == "__main__":  # pragma: no cover - convenience probe
    import json
    import pathlib
    fixture = (pathlib.Path(__file__).resolve().parents[2]
               / "fixtures" / "site-docs" / "sla" / "ingest.html")
    text, found = neutralise(fixture.read_text(encoding="utf-8"))
    print(json.dumps(found, indent=2))
