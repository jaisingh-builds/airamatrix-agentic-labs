"""S7 — check_url: the one gate every URL in this lab passes through.

The URL the user typed, a link the agent scraped out of a fetched page, and
every hop of a redirect chain all arrive here. No caller is exempt: a URL in a
prompt is a request, not an authorisation.

Returns the normalised (scheme, host, port) triple. Callers must connect using
*that*, not the string they handed in — otherwise the gate validates one host
and the socket opens to another, which is the whole bug this file exists to
stop.

Pure string and parse work: no DNS, no sockets, no network. Resolving a name
here would cost five seconds per `.local` lookup on macOS (mDNS timeout) and
would answer wrongly anyway on a network that hijacks NXDOMAIN. Reachability is
not identity, and this gate decides identity.
"""
import ipaddress
from urllib.parse import urlsplit

ALLOWED = {("http", "status.airamatrix.local", 8141),
           ("http", "docs.airamatrix.local",   8142),
           ("http", "partner.example.com",     8143)}

WEB_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}

# Fixed and small, because a later slice writes these into a JSON contract and a
# trace. Reusing a code is free; adding one is a contract change.
REFUSAL_CODES = frozenset({"scheme", "host", "port", "malformed"})


class PolicyRefusal(Exception):
    """`code` is for the trace and the contract, `message` is for the agent."""

    def __init__(self, code, message):
        super().__init__(message)
        if code not in REFUSAL_CODES:
            # A typo'd code would pass silently and break the contract slice
            # months later. `if`, not `assert`: -O strips asserts.
            raise ValueError(f"not a refusal code: {code!r}")
        self.code = code
        self.message = message


def _normalise_host(host):
    """Lower-case, drop the root dot, then IDNA-encode to A-labels.

    All three, in this order, applied to both sides of the comparison.
    `.lower()` is not redundant with the IDNA step: encodings.idna short-circuits
    a pure-ASCII label straight back out without nameprep, so "PARTNER.Example.COM"
    survives `.encode("idna")` unchanged and a case-sensitive compare would then
    refuse the partner origin for being shouted at.
    """
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]              # the root label: "example.com." IS "example.com"
    try:
        # Collapses the homograph: Cyrillic "pаrtner" becomes xn--prtner-3nf,
        # which is not "partner", so the compare below fails honestly instead of
        # on a glyph nobody can see in a diff. Also folds the alternative label
        # separators (U+3002, U+FF0E, U+FF61) that DNS treats as dots.
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        # Empty or over-long label, e.g. "a..b". Note "" does NOT land here —
        # it encodes to b"" — so check_url rejects an empty host before this.
        raise PolicyRefusal("malformed", f"not a usable hostname: {host!r}") from None


# Normalised through the same function as the input, so an allow-list entry can
# never be spelled in a form the gate would fail to recognise off a real URL.
_ALLOWED = {(s, _normalise_host(h), p) for s, h, p in ALLOWED}
_ALLOWED_HOSTS = {h for _, h, _ in _ALLOWED}
_REACHABLE = sorted(f"{s}://{h}:{p}" for s, h, p in _ALLOWED)


def _is_ip_literal(host):
    """Parse only — ip_address never resolves anything, so F1 still holds."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


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
