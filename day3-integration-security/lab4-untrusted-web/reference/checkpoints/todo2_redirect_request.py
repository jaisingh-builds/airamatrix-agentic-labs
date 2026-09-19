"""CHECKPOINT — unblocks TODO 2.

Copy the function below into `web_tools/redirects.py`, replacing the
`raise NotImplementedError("TODO 2: ...")` stub. Copying it in by hand is the
intended use: nothing imports this file, and nothing should.

Copy-out rather than an import or an environment switch on purpose. A second
code path would have to be maintained, tested and explained, and a switch lets
a run finish green without anyone ever reading the code it skipped. Pasting
leaves the evidence in your own file, where a diff shows it.

Paste it back as a METHOD of `_GuardedRedirectHandler` — it is written at
module level here only so this file parses and cannot be imported into a
working object by accident. Indent it four spaces.

Depends only on names already in the starter: _HOPS, RedirectRefused,
TooManyRedirects, self._check, self._max_hops, self._discard.
"""

def redirect_request(self, req, fp, code, msg, headers, newurl):
    """Called once per 30x, before the hop.

    http_error_302 calls this and then `self.parent.open(new)`. Everything
    that touches the destination — DNS, connect, request line, response,
    body — is behind that open(). Raising here is refusal *at the hop*: no
    packet is sent to newurl, so there is no body to have read and no
    response to have leaked into a log.
    """
    hops = getattr(req, _HOPS, 0) + 1

    # Check first, cap second, and the order is load-bearing. A hop that is
    # over the cap AND off the allow-list is a security event wearing a
    # volume symptom's clothes; reporting TooManyRedirects there would bury
    # the one fact an incident reviewer needs — the host the agent was being
    # steered at. `check` is contractually a pure validator, so running it on
    # a hop we may refuse anyway disturbs nothing.
    try:
        # newurl is already absolute and percent-encoded; http_error_302
        # urljoin()s it against the current request before handing it over,
        # so a relative Location cannot smuggle a host past the check.
        self._check(newurl)
    except Exception as exc:
        self._discard(fp)
        raise RedirectRefused(
            newurl, f"refused hop {hops} to {newurl}: {exc}"
        ) from exc

    if hops > self._max_hops:
        self._discard(fp)
        raise TooManyRedirects(
            newurl,
            f"refused hop {hops} to {newurl}: max_hops={self._max_hops}",
        )

    new = super().redirect_request(req, fp, code, msg, headers, newurl)
    if new is not None:             # stdlib never returns None today; mirrored anyway
        setattr(new, _HOPS, hops)
    return new
