"""Lab 4 fixture estate: dns. Four fixture hostnames -> 127.0.0.1, in-process.

Participants run this on managed laptops with no sudo, so /etc/hosts is not an
option. Patching socket.getaddrinfo keeps the mapping inside this interpreter:
nothing outlives the process and no other program on the machine is affected.
"""
import socket

# Mutable on purpose. The DNS-rebinding extension reassigns a value between two
# lookups, to show that a host checked once is not a host that stays checked.
#
# Addresses only, never "host:port". The lab's allow-list is a
# (scheme, host, port) triple; a port smuggled in here would make the host
# component quietly carry two of the three and the triple stop meaning what it
# says.
HOSTS = {
    "status.airamatrix.local": "127.0.0.1",
    "docs.airamatrix.local": "127.0.0.1",
    "partner.example.com": "127.0.0.1",
    "evil.example.net": "127.0.0.1",
}

# The saved original rides on the installed wrapper rather than in a module
# global, so "are we installed?" and "what do we restore?" cannot disagree.
_ORIGINAL = "_lab4_fixture_original"


def install() -> None:
    """Resolve the HOSTS names locally; delegate every other name. Idempotent."""
    original = socket.getaddrinfo
    # Asks the live function, not a bookkeeping flag, whether the patch is on.
    # Skipping this is the classic bug: a second install() wraps the wrapper,
    # and one uninstall() then peels only the outer layer off.
    if hasattr(original, _ORIGINAL):
        return

    # `type` shadows the builtin, and has to: callers pass type=SOCK_STREAM by
    # keyword and the signature must match socket.getaddrinfo exactly.
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        # HOSTS is read per call, not captured at install time, because the
        # rebinding extension mutates it between two lookups of the same name.
        key = host.lower() if isinstance(host, str) else host
        address = HOSTS.get(key)
        if address is None:
            return original(host, port, family, type, proto, flags)
        # Re-enter the real resolver with the literal address instead of
        # hand-building 5-tuples: family/type/proto/flags then produce exactly
        # what the stdlib would, and an IP literal needs no network.
        return original(address, port, family, type, proto, flags)

    setattr(getaddrinfo, _ORIGINAL, original)
    socket.getaddrinfo = getaddrinfo


def uninstall() -> None:
    """Restore the original getaddrinfo object. No-op if not installed."""
    original = getattr(socket.getaddrinfo, _ORIGINAL, None)
    if original is not None:
        socket.getaddrinfo = original
