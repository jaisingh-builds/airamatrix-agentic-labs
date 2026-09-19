"""CHECKPOINT — unblocks TODO 1.

Copy the function below into `web_tools/gate.py`, replacing the
`raise NotImplementedError("TODO 1: ...")` stub. Copying it in by hand is the
intended use: nothing imports this file, and nothing should.

Copy-out rather than an import or an environment switch on purpose. A second
code path would have to be maintained, tested and explained, and a switch lets
a run finish green without anyone ever reading the code it skipped. Pasting
leaves the evidence in your own file, where a diff shows it.

TODO 1 is the one that blocks the lab end to end: fetch_url() checks every
URL before it opens a socket, so an unfinished gate means no signal at all
from the assembled tool or a live run. The per-slice suites are built to be
independent of it — test_web_redirects.py injects its own stub `check` — so
take this when you want to move on to TODOs 2-5 and still run the agent.

Depends only on names already in the starter: PolicyRefusal, WEB_SCHEMES,
DEFAULT_PORTS, _ALLOWED, _ALLOWED_HOSTS, _REACHABLE, _normalise_host,
_is_ip_literal, urlsplit.
"""
from urllib.parse import urlsplit  # already imported in gate.py


def check_url(url):
    """Return the validated (scheme, host, port), or raise PolicyRefusal."""
    if not isinstance(url, str) or not url.strip():
        raise PolicyRefusal("malformed", f"not a URL: {url!r}")

    # Ahead of urlsplit, which silently strips tab/CR/LF. A newline that survives
    # into a request line is header injection, and a gate that never sees the
    # character cannot refuse it.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise PolicyRefusal("malformed",
                            "URL contains whitespace or control characters")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PolicyRefusal("malformed", f"unparseable URL: {exc}") from None

    # Scheme first: file:// and data: have no host at all, and reporting those as
    # a missing host would send the agent looking for the wrong fix.
    scheme = parts.scheme.lower()
    if not scheme:
        raise PolicyRefusal(
            "malformed",
            f"not an absolute URL: {url!r}. Resolve it against the page it came "
            f"from before asking for it.")
    if scheme not in WEB_SCHEMES:
        raise PolicyRefusal(
            "scheme",
            f"scheme {scheme!r} is not fetchable; only http and https are. "
            f"Reachable: {_REACHABLE}")

    # In an authority, "@" only ever ends userinfo, and the host is what FOLLOWS
    # it: "partner.example.com@evil.example.net" reads as partner and connects to
    # evil. A substring check falls for it, and so does a human skimming a log.
    # Nothing in this lab has a legitimate userinfo, so refuse the shape outright
    # rather than quietly honouring the right-hand side.
    if "@" in parts.netloc:
        raise PolicyRefusal(
            "malformed",
            f"URL carries userinfo before '@'; the host it would actually reach "
            f"is {(parts.hostname or '?')!r}. Name the host directly.")

    host = parts.hostname
    if not host:
        raise PolicyRefusal("malformed", f"URL has no host: {url!r}")

    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyRefusal("malformed", f"bad port in {url!r}: {exc}") from None
    if port is None:
        # RFC 6454: an origin's port is the *effective* port, so an absent one is
        # the scheme default, not a wildcard. http://partner.example.com/ means
        # port 80, and 80 is not on the list. Resolving it here rather than
        # special-casing None keeps the refusal honest — the host is right and
        # the port is the one thing to fix, so the code below says "port".
        port = DEFAULT_PORTS[scheme]

    host = _normalise_host(host)

    if host not in _ALLOWED_HOSTS:
        # No denylist of IP spellings anywhere in this file: matching exact names
        # refuses 127.0.0.1, 0x7f.0.0.1, [::1], 2130706433 and every notation
        # nobody has thought of yet, for free. The literal check below only picks
        # the wording — the decision is the allow-list's either way, which is why
        # 0x7f.0.0.1 (which ip_address rejects) is still refused.
        hint = (" — an IP literal; this gate matches names, and re-spelling the "
                "address in another notation reaches the same place"
                if _is_ip_literal(host) else "")
        raise PolicyRefusal(
            "host",
            f"host {host!r} is not on the allow-list{hint}. Reachable: {_REACHABLE}")

    triple = (scheme, host, port)
    if triple not in _ALLOWED:
        # Host is known, so name the fix precisely instead of re-printing the
        # whole list and making the agent diff it.
        ports = sorted(p for s, h, p in _ALLOWED if s == scheme and h == host)
        if ports:
            raise PolicyRefusal(
                "port",
                f"host {host!r} is reachable over {scheme} on {ports}, not {port}.")
        raise PolicyRefusal(
            "scheme",
            f"host {host!r} is not reachable over {scheme!r}. Reachable: {_REACHABLE}")

    return triple
