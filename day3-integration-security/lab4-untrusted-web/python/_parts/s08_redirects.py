"""S8 — a redirect is a fetch, so every hop gets checked.

urllib follows redirects silently. `urlopen("http://trusted/x")` will read bytes
from `http://evil/y` and hand them back with nothing in the return value saying
so. A gate that validates the url it was given and then calls urlopen has
therefore checked one host and fetched another: the check is real, and it is
attached to the wrong request. This module changes that default.

    opener = guarded_opener(check)      # check(url) raises on refusal
    opener.open(url)                    # every Location re-checked before the hop

`check` is injected, not imported. The allow-list (S7) and the redirect policy
are separate concerns: one answers "may we talk to this origin", the other
"whose question is it anyway". Keeping them apart also means this slice can be
tested with a stub, and a caller can carry a narrower list at one call site.

Contract assumed of `check`: a callable taking one absolute url string, raising
anything at all to refuse, and otherwise pure — it is a validator, not an
action, and this module may call it for a hop it then refuses anyway.
"""
import urllib.request

# The hop count rides on the Request object, because urllib already threads that
# object from one hop to the next for us. A counter on the handler would be
# shared by every concurrent open() through the same opener.
_HOPS = "_s08_hops"


class RedirectRefused(Exception):
    """A hop was refused before it was taken.

    Deliberately NOT a URLError/OSError subclass. `except URLError: retry` is how
    callers absorb a flaky network, and a blocked exfiltration hop disappearing
    into a retry loop is the failure this whole slice exists to prevent.
    """

    def __init__(self, url, message):
        super().__init__(message)
        self.url = url          # the Location we would have followed, for the audit line


class TooManyRedirects(RedirectRefused):
    """The hop cap tripped on a hop that was otherwise allowed. Separate from a
    refusal by `check` because the causes differ: a loop that stays inside the
    allow-list is still a loop, and no allow-list can ever end one."""


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Replaces the stdlib redirect handler. Same 30x coverage (301/302/303/307/
    308 all route through http_error_302), one added question before each hop."""

    def __init__(self, check, max_hops):
        self._check = check
        self._max_hops = max_hops
        # The stdlib's own loop guards live in http_error_302, fire *after*
        # redirect_request returns, and raise HTTPError — which a caller would
        # read as a server error rather than a refusal. Held above our cap so
        # ours always trips first and the limit the caller asked for is the one
        # that applies.
        self.max_repeats = max_hops + 1
        self.max_redirections = max_hops + 1

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

    @staticmethod
    def _discard(fp):
        """http_error_302 closes the 30x response only on the path where it
        follows the hop. We leave by exception, so close it here or the socket
        sits open until the garbage collector notices."""
        try:
            fp.close()
        except Exception:
            pass                        # cleanup on an error path must not mask the refusal


def guarded_opener(check, max_hops=3):
    """An OpenerDirector that re-checks the destination at every redirect.

    check(url) runs on each Location before it is followed and may raise
    anything; the refusal surfaces as RedirectRefused. A hop that `check`
    allows but that runs past max_hops is cut with TooManyRedirects (a subclass,
    so one `except RedirectRefused` catches both). Where both apply, the
    allow-list wins: an off-list destination is reported by name.

    The FIRST url is not checked here. The caller already had that one in hand
    and approving it is the gate's job; this handler owns only the hops the
    caller never saw.
    """
    return urllib.request.build_opener(
        # An empty proxy map, not the environment's. An http_proxy variable
        # sends every request to one host while the url still names another —
        # the same check-here-fetch-there gap this module closes, arriving
        # through a different door.
        urllib.request.ProxyHandler({}),
        _GuardedRedirectHandler(check, max_hops),
    )
