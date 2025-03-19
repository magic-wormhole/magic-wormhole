from hypothesis.stateful import rule, precondition, RuleBasedStateMachine, run_state_machine_as_test
from hypothesis.strategies import integers, lists
from hypothesis import given
import pytest
import pytest_twisted

from wormhole.errors import LonelyError
from twisted.internet.testing import MemoryReactorClock
from twisted.internet.interfaces import IHostnameResolver
from wormhole.eventual import EventualQueue
from wormhole import create



from twisted.internet import defer
defer.setDebugging(True)


client_to_mailbox = [
    {"type": "claim", },
    {"type": "allocate", },
    {"type": "open", "mailbox_id": None},
    {"type": "add", },
    {"type": "release", },
    {"type": "close", "mailbox_id": None, "mood": None},
]

mailbox_to_client = [
    {"type": "welcome", },
    {"type": "claimed", },
    {"type": "allocated", },
    {"type": "opened", },
    {"type": "nameplates", },
    {"type": "ack", },
    {"type": "error", },
    {"type": "message", "side": None, "phase": None},
    {"type": "released", },
    {"type": "closed", },
]

class WormholeMachine(RuleBasedStateMachine):
    def __init__(self,wormhole, reactor):
        RuleBasedStateMachine.__init__(self)
        self._reactor = reactor
        self._pending_wormhole = wormhole
        self.wormhole = None
        self._transcript = []

    @rule() # how to connect to welcome?
    @precondition(lambda self: self.wormhole is None)
    def new_wormhole(self):
        self._transcript.append("new")
        self.wormhole = self._pending_wormhole
        assert self.wormhole._boss is not None

    @rule()
    @precondition(lambda self: self.wormhole) # can't run this transition/check until we have a wormhole
    def welcome(self):
        # we haven't recv'd a welcome yet
        d = self.wormhole.get_welcome() # we extract a deferred that will be called when we get a welcome message
        assert not d.called # on a deferred there's a "called"
        self.wormhole._boss.rx_welcome({"type": "welcome", "motd": "hello, world"})

        self._reactor.advance(1)
        assert d.called # now we have a welcome message!
        self._transcript.append("welcome")
        self.wormhole._boss.rx_welcome({"type": "welcome", "motd": "hello, world"})




from twisted.internet.address import IPv4Address
from twisted.internet._resolver import HostResolution  # "internal" class, but it's simple
from twisted.internet.interfaces import ISSLTransport, IReactorPluggableNameResolver
from zope.interface import directlyProvides, implementer


@implementer(IHostnameResolver)
class _StaticTestResolver(object):
    def resolveHostName(self, receiver, hostName, portNumber=0):
        """
        Implement IHostnameResolver which always returns 127.0.0.1:31337
        """
        resolution = HostResolution(hostName)
        receiver.resolutionBegan(resolution)
        receiver.addressResolved(
            IPv4Address('TCP', '127.0.0.1', 31337 if portNumber == 0 else portNumber)
        )
        receiver.resolutionComplete()


@implementer(IReactorPluggableNameResolver)
class _TestNameResolver(object):
    """
    A test version of IReactorPluggableNameResolver
    """

    _resolver = None

    @property
    def nameResolver(self):
        if self._resolver is None:
            self._resolver = _StaticTestResolver()
        return self._resolver

    def installNameResolver(self, resolver):
        old = self._resolver
        self._resolver = resolver
        return old


class MemoryReactorClockResolver(MemoryReactorClock, _TestNameResolver):
    """
    Combine MemoryReactor, Clock and an IReactorPluggableNameResolver
    together.
    """
    pass


def test_foo(mailbox):

    reactor = MemoryReactorClockResolver()
    eq = EventualQueue(reactor)
    w = create("foo", "ws://fake:1234/v1", reactor, _eventual_queue=eq)

    machines = []

    def create_machine():
        m = WormholeMachine(w, reactor)
        machines.append(m)
        return m
    run_state_machine_as_test(create_machine)
