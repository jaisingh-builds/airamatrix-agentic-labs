"""CHECKPOINT — unblocks TODO 3.

Copy the function below into `web_tools/envelope.py`, replacing the
`raise NotImplementedError("TODO 3: ...")` stub. Copying it in by hand is the
intended use: nothing imports this file, and nothing should.

Copy-out rather than an import or an environment switch on purpose. A second
code path would have to be maintained, tested and explained, and a switch lets
a run finish green without anyone ever reading the code it skipped. Pasting
leaves the evidence in your own file, where a diff shows it.

TODO 4 (neutralise) and TODO 5 (the ceilings) are testable without it, but
fetch_url() returns the envelope, so the assembled tool and the live run
both stop here.

Depends only on names already in the starter: secrets, NONCE_BYTES,
_RESERVED_ATTRS, _SAFE_ATTR_NAME, _escape_attr, _render.
"""
import secrets  # already imported in envelope.py


def wrap_untrusted(body, source, **meta):
    """Return body inside an envelope it cannot close.

    The id is generated here, on every call, rather than once per run: a per-run
    nonce means one leaked wrapper — one page that got its envelope echoed back
    into a later fetch — unlocks every remaining result in that run.
    """
    # secrets, not random: random's Mersenne state is reconstructible from a few
    # outputs and is seeded predictably often enough to matter, and a guessable
    # nonce is exactly as useful to an attacker as no nonce.
    nonce = secrets.token_hex(NONCE_BYTES)

    attrs = ['id="%s"' % nonce, 'source="%s"' % _escape_attr(source)]
    for key, value in meta.items():
        if key in _RESERVED_ATTRS:
            # A second source= would shadow the real origin in whatever reads
            # the tag. Today the signature catches that one first; this stays
            # so the guarantee survives a later slice widening the signature.
            raise ValueError("meta key %r collides with an envelope attribute" % key)
        if not _SAFE_ATTR_NAME.match(key):
            raise ValueError("meta key %r is not a safe attribute name" % key)
        attrs.append('%s="%s"' % (key, _escape_attr(_render(value))))

    # Body goes in verbatim. Newlines around it keep the delimiters on their own
    # lines without touching a single byte the site sent.
    return '<untrusted %s>\n%s\n</untrusted id="%s">' % (" ".join(attrs), body, nonce)
