import random

from twisted.internet.defer import inlineCallbacks
from twisted.internet.endpoints import serverFromString
from twisted.internet.error import CannotListenError
from twisted.internet.interfaces import IStreamServerEndpoint
from zope.interface import implementer

# Something else may grab a port between our choice and the listen() call,
# so we retry a few times. We also have to give up eventually, because the
# reason we can't listen might be one that retrying won't fix.
LISTEN_ATTEMPTS = 5


def parse_port_range(value):
    """
    Parse ``"PORT"`` or ``"LOW-HIGH"`` into an inclusive ``(low, high)``
    tuple, raising ValueError if it isn't a valid range of TCP ports.
    """
    low, sep, high = value.strip().partition("-")
    try:
        low = int(low)
        high = int(high) if sep else low
    except ValueError:
        raise ValueError(
            f"invalid port range {value!r}: expected PORT or LOW-HIGH"
        )
    if not 1 <= low <= high <= 65535:
        raise ValueError(
            f"invalid port range {value!r}: ports must be in 1-65535,"
            " with the lower port first"
        )
    return (low, high)


def candidate_ports(port_range, attempts=LISTEN_ATTEMPTS, rng=random):
    """
    Return up to ``attempts`` distinct ports from the inclusive
    ``port_range``, in random order.
    """
    low, high = port_range
    ports = range(low, high + 1)
    return rng.sample(ports, min(attempts, len(ports)))


@inlineCallbacks
def listen_in_port_range(reactor, factory, port_range,
                         attempts=LISTEN_ATTEMPTS):
    """
    Listen for TCP connections on a port from ``port_range``.

    :returns: a Deferred that fires with the IListeningPort, or fails with
        CannotListenError if none of the ports we tried was available.
    """
    ports = candidate_ports(port_range, attempts)
    last_error = None
    for port in ports:
        ep = serverFromString(reactor, f"tcp:{port}")
        try:
            listening_port = yield ep.listen(factory)
        except CannotListenError as e:
            last_error = e.socketError
            continue
        return listening_port
    low, high = port_range
    raise CannotListenError(
        "", f"{low}-{high}",
        f"tried {len(ports)} port(s), none was free (last error: {last_error})",
    )


@implementer(IStreamServerEndpoint)
class PortRangeServerEndpoint:
    """
    A TCP server endpoint that listens on a free port from a range,
    like ``serverFromString(reactor, "tcp:0")`` does for the whole
    ephemeral range.
    """

    def __init__(self, reactor, port_range, attempts=LISTEN_ATTEMPTS):
        self._reactor = reactor
        self._port_range = port_range
        self._attempts = attempts

    def listen(self, factory):
        return listen_in_port_range(self._reactor, factory,
                                    self._port_range, self._attempts)
