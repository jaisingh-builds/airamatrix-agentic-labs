"""Small helpers that keep the output identical to the Java and Node solutions.

Java formats numbers HALF_UP (String.format, Math.round); Python rounds half to even. A duty manager
comparing `$0.125` in two terminals should not see two answers, so every printed number goes through here.
"""
import json, math
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


class ArgError(ValueError):
    """Bad input from the person at the keyboard - printed as `refused: <message>`, exit 3."""


class SetupError(RuntimeError):
    """Missing environment, nothing to run against - printed as the message, exit 2."""


def jround(x, places=0):
    """Math.round-style half-up rounding (places=4 for cost_usd in JSON)."""
    f = 10 ** places
    v = math.floor(x * f + 0.5)
    return int(v) if places == 0 else v / f


def jfmt(x, places):
    """String.format("%.Nf") - half-up on the shortest decimal form, like Java."""
    return str(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def dumps(v):
    """Compact JSON, the same bytes Jackson's toString() writes (non-ASCII kept as is)."""
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def canonical(v):
    """Keys sorted recursively, no whitespace - what the proposal hash is taken over."""
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def cut(s, n):
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[:n] + f"...[+{len(s) - n} chars]"


def java_list(items):
    """List.toString(): [a, b]"""
    return "[" + ", ".join(str(i) for i in items) + "]"


def as_text(v):
    """JsonNode.asText(): strings as is, numbers/booleans as JSON, missing/null as ''."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return ""


def parse_instant(s):
    try:
        t = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        t = None
    if t is None or t.tzinfo is None:
        raise ArgError(f"as_of must be ISO-8601 with an offset, e.g. 2026-09-24T10:30:00+05:30 (got {'null' if s is None else s})")
    return t


def iso(t):
    """yyyy-MM-dd'T'HH:mm:ssxxx - e.g. 2026-09-24T10:30:00+05:30 (Java Sla.ISO)."""
    return t.isoformat(timespec="seconds")


def minutes_between(a, b):
    """Duration.between(a, b).toMinutes(): whole minutes, truncated toward zero."""
    secs = int((b - a).total_seconds())
    return secs // 60 if secs >= 0 else -((-secs) // 60)
