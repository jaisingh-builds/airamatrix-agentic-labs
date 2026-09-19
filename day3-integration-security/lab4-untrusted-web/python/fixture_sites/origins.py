"""Lab 4 fixture estate: the three trusted origins.

    status   8141   fixtures/site-status/   the entry point, links to the rest
    docs     8142   fixtures/site-docs/     the SLA page, and the injection bait
    partner  8143   fixtures/site-partner/  the burst figure, behind a redirect

Static files come off disk; everything below is the behaviour a static server
cannot give us — two redirects that must be told apart, and an oversized body
that has to be generated rather than committed.

Safe to call repeatedly: if a port is already serving, it is reused.
"""
import functools
import http.server
import json
import pathlib
import socket
import threading
import urllib.parse

STATUS_PORT = 8141
DOCS_PORT = 8142
PARTNER_PORT = 8143

ROOT = pathlib.Path(__file__).resolve().parents[2] / "fixtures"
SITES = {
    "status": (STATUS_PORT, ROOT / "site-status"),
    "docs": (DOCS_PORT, ROOT / "site-docs"),
    "partner": (PARTNER_PORT, ROOT / "site-partner"),
}

# The sink is fixture_sites.sink's concern; this module only needs somewhere
# off-allow-list to point a redirect at, so it takes the URL and nothing else.
EXFIL_URL = "http://evil.example.net:8144/collect"

BIG_TARGET_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024

_servers = {}

# ---------------------------------------------------------------- byte counter

_counter_lock = threading.Lock()
_bytes_written = 0


def bytes_written() -> int:
    """Body bytes this process has pushed at a client from the big endpoints."""
    with _counter_lock:
        return _bytes_written


def reset_byte_counter() -> None:
    """Zero the counter so one test's stream is not another test's evidence."""
    global _bytes_written
    with _counter_lock:
        _bytes_written = 0


def _write_counted(handler, chunks):
    """Stream chunks, counting each one only after its write has returned.

    Counting a chunk before writing it would count intent: the point of this
    number is that a client which stops reading leaves it far below the body
    size, which is what makes it evidence that a read cap acted during the read.
    """
    global _bytes_written
    try:
        for chunk in chunks:
            handler.wfile.write(chunk)
            with _counter_lock:
                _bytes_written += len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass            # client hung up mid-stream; the count so far IS the finding


# ------------------------------------------------------------- big body bodies

_PAD = "q" * 512
_ROW_TEMPLATE = '{"i":"%07d","pad":"' + _PAD + '"}'
_ROW_LEN = len(_ROW_TEMPLATE % 0)      # fixed width, so Content-Length is arithmetic
_JSON_HEAD = '{"note":"oversized fixture body - generated, never committed","rows":['
_JSON_TAIL = ']}'
# Rows are comma-separated, so every row after the first costs one extra byte.
_ROW_COUNT = max(
    1,
    (BIG_TARGET_BYTES - len(_JSON_HEAD) - len(_JSON_TAIL) + 1) // (_ROW_LEN + 1),
)
BIG_JSON_BYTES = len(_JSON_HEAD) + _ROW_COUNT * _ROW_LEN + (_ROW_COUNT - 1) + len(_JSON_TAIL)

_TXT_LINE = ("filler %07d " + "q" * 48 + "\n")
_TXT_LINE_LEN = len(_TXT_LINE % 0)
_TXT_LINE_COUNT = max(1, BIG_TARGET_BYTES // _TXT_LINE_LEN)
BIG_TXT_BYTES = _TXT_LINE_COUNT * _TXT_LINE_LEN


def _batched(pieces):
    """Group small strings into ~64 KB writes.

    One write per row would be ~16k syscalls; one write for the whole body would
    defeat the point of streaming it.
    """
    buf, size = [], 0
    for piece in pieces:
        buf.append(piece)
        size += len(piece)
        if size >= _CHUNK_BYTES:
            yield "".join(buf).encode()
            buf, size = [], 0
    if buf:
        yield "".join(buf).encode()


def _big_json_pieces():
    yield _JSON_HEAD
    for i in range(_ROW_COUNT):
        yield ("," if i else "") + (_ROW_TEMPLATE % i)
    yield _JSON_TAIL


def _big_txt_pieces():
    for i in range(_TXT_LINE_COUNT):
        yield _TXT_LINE % i


# ------------------------------------------------------------------- endpoints

def _redirect(handler, location):
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _capacity_redirect(handler):
    """302 to the same host. Absolute, and built from the request's own Host.

    Hardcoding 127.0.0.1 here would turn this into a cross-host redirect the
    moment the lab is reached through a fixture hostname, silently deleting the
    followable half of the redirect exercise.
    """
    host = handler.headers.get("Host") or f"127.0.0.1:{handler.server.server_port}"
    _redirect(handler, f"http://{host}/capacity.json")


def _legacy_redirect(handler):
    """302 to a host that is not on the allow-list. Must stay refusable."""
    _redirect(handler, EXFIL_URL)


def _stream_body(handler, content_type, pieces, declared_length):
    """Serve one oversized body, with or without a declared size.

    declared_length=None is the variant that matters: a body whose size is
    advertised can be refused on the header alone, which is a *different*
    control from capping during the read. A lying Content-Length costs an
    attacker nothing, so a cap that only ever sees an honest header defends
    against nobody.
    """
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    if declared_length is None:
        # EOF is the only thing delimiting this body, so the connection has to
        # close. send_header also flips close_connection, which is what stops a
        # keep-alive reader waiting forever for a body that already ended.
        handler.send_header("Connection", "close")
    else:
        handler.send_header("Content-Length", str(declared_length))
    handler.end_headers()
    _write_counted(handler, _batched(pieces))


def _big_json(handler):
    _stream_body(handler, "application/json", _big_json_pieces(), BIG_JSON_BYTES)


def _big_txt(handler):
    _stream_body(handler, "text/plain", _big_txt_pieces(), BIG_TXT_BYTES)


def _big_json_nocl(handler):
    _stream_body(handler, "application/json", _big_json_pieces(), None)


def _big_txt_nocl(handler):
    _stream_body(handler, "text/plain", _big_txt_pieces(), None)


# (port, path) rather than path alone: /big.json belongs to the status origin and
# must 404 on the other two, or "which origin served this" stops being a question.
_ROUTES = {
    (PARTNER_PORT, "/capacity"): _capacity_redirect,
    (PARTNER_PORT, "/legacy"): _legacy_redirect,
    (STATUS_PORT, "/big.json"): _big_json,
    (STATUS_PORT, "/big.txt"): _big_txt,
    (STATUS_PORT, "/big-nocl.json"): _big_json_nocl,
    (STATUS_PORT, "/big-nocl.txt"): _big_txt_nocl,
}


class _OriginHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        route = _ROUTES.get((self.server.server_port, path))
        if route is None:
            return super().do_GET()
        route(self)

    # Pinned, not inherited: the no-Content-Length bodies are delimited by EOF,
    # and HTTP/1.1 keep-alive would leave their readers hanging.
    protocol_version = "HTTP/1.0"

    def log_message(self, *args, **kwargs):
        pass            # quiet: 16k-row streams would bury the test output


class _ThreadedOrigin(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # An abandoned 8 MB stream blocks its thread until the socket resets. On a
    # single-threaded server that stalls every later request in the same run.


def _port_is_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def serve_in_background():
    """Start the three origins, skipping any port already being served."""
    for name, (port, root) in SITES.items():
        if name in _servers or _port_is_open(port):
            continue
        handler = functools.partial(_OriginHandler, directory=str(root))
        _servers[name] = _ThreadedOrigin(("127.0.0.1", port), handler)
        threading.Thread(target=_servers[name].serve_forever, daemon=True).start()
    return _servers


def base_url(site: str) -> str:
    """Loopback base for one origin. Hostnames are fixture_sites.dns's concern."""
    return f"http://127.0.0.1:{SITES[site][0]}"


if __name__ == "__main__":
    serve_in_background()
    for _name, (_port, _root) in SITES.items():
        print(f"{_name:8} http://127.0.0.1:{_port}  <- {_root}")
    print(f"big.json {BIG_JSON_BYTES} bytes, big.txt {BIG_TXT_BYTES} bytes (streamed)")
    print("big-nocl.json / big-nocl.txt: same bodies, no Content-Length")
    threading.Event().wait()
