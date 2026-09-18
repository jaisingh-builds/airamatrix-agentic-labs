"""Tiny local HTTP server so the http_get tool has something deterministic to
call. Real endpoint, real HTTP, no dependency on the classroom network.

Safe to call repeatedly: if the port is already serving, it reuses it.
"""
import functools, http.server, pathlib, socket, threading

PORT = 8137
ROOT = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
_server = None


def _port_is_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def serve_in_background(port: int = PORT):
    """Start the fixture server unless something is already on the port."""
    global _server
    if _server is not None or _port_is_open(port):
        return _server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
    handler.log_message = lambda *a, **k: None          # quiet
    http.server.HTTPServer.allow_reuse_address = True
    _server = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server


if __name__ == "__main__":
    serve_in_background()
    print(f"serving {ROOT} on http://127.0.0.1:{PORT}")
    threading.Event().wait()
