"""Lab 4 fixture estate: sink. The attacker's collection server.

Listens on 8144, accepts anything, refuses nothing, and appends one JSON line
per request to fixtures/.sink.log.

The lab's headline claim is "the agent never reached the attacker", and this log
is the evidence for it. An empty log only proves something if the server would
have recorded the request had it arrived — so the sink must be unmistakably
willing. A sink that 404s or 501s is indistinguishable from a working
allow-list, and would make the claim unfalsifiable.

Safe to call repeatedly: if the port is already serving, it reuses it.
"""
import datetime
import http.server
import json
import pathlib
import socket
import sys
import threading

PORT = 8144
ROOT = pathlib.Path(__file__).resolve().parents[2] / "fixtures"
LOG = ROOT / ".sink.log"

_server = None
_write_lock = threading.Lock()   # concurrent fetches must not interleave one line


def log_path() -> pathlib.Path:
    """Where the evidence lands. Gitignored — it holds whatever leaked."""
    return LOG


def read_log() -> list:
    """Records oldest first. A missing file reads as no traffic, not an error:
    "the sink was never called" is the expected outcome, not a broken fixture."""
    if not LOG.exists():
        return []
    lines = LOG.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def truncate_log() -> None:
    """Call from setUp, per test — never once per run. Three suites read this
    file; a line left by the previous test is a false failure."""
    ROOT.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")


def _append(record: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str)   # default= so an odd value cannot lose the record
    with _write_lock:
        with LOG.open("a", encoding="utf-8") as handle:   # "a", never "w": evidence accumulates
            handle.write(line + "\n")


class _SinkHandler(http.server.BaseHTTPRequestHandler):
    """Records every request, answers 200 to all of them."""

    protocol_version = "HTTP/1.0"    # close per request; no keep-alive to strand a caller

    def log_message(self, *args):    # quiet: the log file is the record, not stderr
        pass

    def __getattr__(self, name):
        """BaseHTTPRequestHandler dispatches to do_<VERB> and sends 501 when it
        is missing. Answering every do_* is what stops an unusual method looking
        like a rejection the agent's allow-list made."""
        if name.startswith("do_"):
            return self._collect
        raise AttributeError(name)

    def _collect(self):
        body = self._read_body()
        _append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "method": self.command,
            "path": self.path,       # query string included: that is where a secret rides
            "body": body,
            "user_agent": self.headers.get("User-Agent", ""),
            "client": self.client_address[0],
        })
        payload = b"ok"
        self.send_response(200)      # never 404 — see the module docstring
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":   # HEAD carries headers only
            self.wfile.write(payload)

    def _read_body(self) -> str:
        """Drain the body. Undecodable bytes are replaced and a bad length is
        treated as none — a sink that raises on a hostile payload destroys the
        evidence it exists to capture."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", "replace")


def _port_is_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def owns_server() -> bool:
    """True only if THIS process bound the sink.

    Read this before asserting the log is empty. A borrowed sink writes to the
    same file, so the assertion still holds — but a sink that is not running at
    all also produces an empty log, and that is indistinguishable from a working
    allow-list. `owns_server()` plus a liveness probe is what separates them.
    """
    return _server is not None


def serve_in_background(port: int = PORT):
    """Start the sink unless something is already on the port.

    Returns the server, or None when the port was already being served by
    another process — see owns_server(). That borrowed server dies when its
    owner exits.
    """
    global _server
    if _server is not None:
        return _server
    if _port_is_open(port):
        print("fixture_sites.sink: port 8144 already served by another process; "
              "borrowing it. It dies when its owner exits. Run lab suites "
              "sequentially.", file=sys.stderr)
        return None
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    _server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _SinkHandler)
    _server.daemon_threads = True   # a wedged request must not outlive the test run
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server


if __name__ == "__main__":
    serve_in_background()
    print(f"sink on http://127.0.0.1:{PORT}, logging to {LOG}")
    threading.Event().wait()
