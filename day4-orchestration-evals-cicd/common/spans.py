"""
Tracing spans for agent runs - one JSON line per span, standard library only.

    from spans import Tracer
    tr = Tracer("lab5-1", trace_id=run_id)
    with tr.span("stage.investigate", account="ACC-1001") as s:
        ...
        s.set(cost_usd=0.03, tool_calls=4)

A span is a named, timed unit of work with a parent. Nested spans form a tree:
run -> stage -> model turn / tool call / HTTP write. `trace_view.py` prints it.

What goes in, and what never does:
  * attributes are REDACTED at write time: bearer tokens, sk- keys, 32+ hex
    secrets and the value of any environment variable whose name says it is a
    secret. Redaction at the sink is the only place it can't be forgotten.
  * long strings are cut to MAX_ATTR chars - a trace is for diagnosis, not a
    second copy of every customer ticket.
"""
import contextlib, json, os, re, time, uuid
from pathlib import Path

MAX_ATTR = 600
# Names that say "secret". Not bare "key": {"key": "ingest.max_concurrent_jobs"} is a config key,
# and the first version of this file redacted it - over-redaction is a bug too (you can't debug).
_SECRET_NAMES = re.compile(r"(token|secret|passw(or)?d|credential|api_?key|private_?key|auth|_key$)", re.I)
_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-[REDACTED]"),
    (re.compile(r"\b[0-9a-f]{32,}\b"), "[REDACTED-HEX]"),
]

def _secret_values():
    return [v for k, v in os.environ.items() if _SECRET_NAMES.search(k) and len(v) >= 8]

def redact(value, limit=MAX_ATTR):
    """Mask secrets in any JSON-able value. Applied to every attribute on write.
    limit=None keeps the full length (for text that is sent on, e.g. a diff)."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_NAMES.search(str(k)) else redact(v, limit)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, limit) for v in value]
    if not isinstance(value, str):
        return value
    for secret in _secret_values():
        value = value.replace(secret, "[REDACTED]")
    for pat, repl in _PATTERNS:
        value = pat.sub(repl, value)
    if limit and len(value) > limit:
        value = value[:limit] + f"...[+{len(value) - limit} chars]"
    return value

class Span:
    def __init__(self, tracer, name, parent_id, attrs):
        self.tracer, self.name, self.parent_id = tracer, name, parent_id
        self.span_id = uuid.uuid4().hex[:12]
        self.attrs, self.status, self.error = dict(attrs), "ok", None
        self.start = time.time()

    def set(self, **attrs):
        self.attrs.update(attrs)

    def fail(self, error):
        self.status, self.error = "error", str(error)[:300]

class Tracer:
    def __init__(self, name, trace_id=None, root=None):
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        root = Path(root or os.environ.get("LAB_TRACE_DIR") or Path(__file__).resolve().parents[2] / "traces")
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{name}-{self.trace_id}.jsonl"
        self._stack = []

    @contextlib.contextmanager
    def span(self, name, **attrs):
        s = Span(self, name, self._stack[-1].span_id if self._stack else None, attrs)
        self._stack.append(s)
        try:
            yield s
        except BaseException as e:
            if s.status == "ok":
                s.fail(f"{type(e).__name__}: {e}")
            raise
        finally:
            self._stack.pop()
            self._write(s)

    def event(self, name, **attrs):
        """A zero-duration span: a tool call reported by the SDK, a decision, an approval."""
        with self.span(name, **attrs):
            pass

    def _write(self, s):
        rec = {"trace_id": self.trace_id, "span_id": s.span_id, "parent_id": s.parent_id,
               "name": s.name, "start": round(s.start, 3), "duration_ms": round((time.time() - s.start) * 1000),
               "status": s.status, "error": s.error, "attrs": redact(s.attrs)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
