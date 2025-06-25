import io
from binascii import hexlify, unhexlify

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from twisted.internet import address, defer, endpoints, error, protocol, task
from twisted.internet.defer import gatherResults
from twisted.test import proto_helpers

from unittest import mock
from wormhole_transit_relay import transit_server
from pytest_twisted import ensureDeferred

from .. import transit
from .._hints import DirectTCPV1Hint
from ..errors import InternalError
from ..util import HKDF
import pytest


@ensureDeferred
async def test_one_winner():
    cancelled = set()
    contenders = [
        defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(5)
    ]
    d = transit.there_can_be_only_one(contenders)
    assert not d.called
    contenders[0].errback(ValueError())
    assert not d.called
    contenders[1].errback(TypeError())
    assert not d.called
    contenders[2].callback("yay")
    assert await d == "yay"
    assert cancelled == set([3, 4])


@ensureDeferred
async def test_there_might_also_be_none():
    cancelled = set()
    contenders = [
        defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
    ]
    d = transit.there_can_be_only_one(contenders)
    assert not d.called
    contenders[0].errback(ValueError())
    assert not d.called
    contenders[1].errback(TypeError())
    assert not d.called
    contenders[2].errback(TypeError())
    assert not d.called
    contenders[3].errback(NameError())
    with pytest.raises(ValueError):
        await d  # first failure is recorded
    assert cancelled == set()


@ensureDeferred
async def test_cancel_early():
    cancelled = set()
    contenders = [
        defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
    ]
    d = transit.there_can_be_only_one(contenders)
    assert not d.called
    assert cancelled == set()
    d.cancel()
    with pytest.raises(defer.CancelledError):
        await d
    assert cancelled == set(range(4))


@ensureDeferred
async def test_cancel_after_one_failure():
    cancelled = set()
    contenders = [
        defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
    ]
    d = transit.there_can_be_only_one(contenders)
    assert not d.called
    assert cancelled == set()
    contenders[0].errback(ValueError())
    d.cancel()
    with pytest.raises(ValueError):
        await d
    assert cancelled == set([1, 2, 3])


@pytest.fixture()
def forever():
    clock = task.Clock()
    c = transit.Common("", reactor=clock)
    cancelled = []
    d0 = defer.Deferred(cancelled.append)
    d = c._not_forever(1.0, d0)
    yield c, clock, d0, d, cancelled


@ensureDeferred
async def test_not_forever_fires(forever):
    c, clock, d0, d, cancelled = forever
    assert not d.called
    assert cancelled == []
    d.callback(1)
    answer = await d
    assert answer == 1
    assert cancelled == []
    assert not clock.getDelayedCalls()


@ensureDeferred
async def test_not_forever_errs(forever):
    c, clock, d0, d, cancelled = forever
    assert not d.called
    assert cancelled == []
    d.errback(ValueError())
    assert cancelled == []
    with pytest.raises(ValueError):
        await d
    assert not clock.getDelayedCalls()


@ensureDeferred
async def test_not_forever_cancel_early(forever):
    c, clock, d0, d, cancelled = forever
    assert not d.called
    assert cancelled == []
    d.cancel()
    assert cancelled == [d0]
    with pytest.raises(defer.CancelledError):
        await d
    assert not clock.getDelayedCalls()


@ensureDeferred
async def test_not_forever_timeout(forever):
    c, clock, d0, d, cancelled = forever
    assert not d.called
    assert cancelled == []
    clock.advance(2.0)
    assert cancelled == [d0]
    with pytest.raises(defer.CancelledError):
        await d
    assert not clock.getDelayedCalls()


def test_allocate_port():
    portno = transit.allocate_tcp_port()
    assert isinstance(portno, int)


def test_allocate_port_no_reuseaddr():
    mock_sys = mock.Mock()
    mock_sys.platform = "cygwin"
    with mock.patch("wormhole.transit.sys", mock_sys):
        portno = transit.allocate_tcp_port()
    assert isinstance(portno, int)


LOOPADDR = "127.0.0.1"
OTHERADDR = "1.2.3.4"


@ensureDeferred
async def test_relay_hints():
    URL = "tcp:host:1234"
    c = transit.Common(URL, no_listen=True)
    hints = await c.get_connection_hints()
    assert hints == [{
        "type":
        "relay-v1",
        "hints": [{
            "type": "direct-tcp-v1",
            "hostname": "host",
            "port": 1234,
            "priority": 0.0
        }],
    }]
    with pytest.raises(InternalError):
        transit.Common(123)


@ensureDeferred
async def test_no_relay_hints():
    c = transit.Common(None, no_listen=True)
    hints = await c.get_connection_hints()
    assert hints == []


def test_ignore_bad_hints():
    c = transit.Common("")
    c.add_connection_hints([{"type": "unknown"}])
    c.add_connection_hints([{
        "type": "relay-v1",
        "hints": [{
            "type": "unknown"
        }]
    }])
    assert c._their_direct_hints == []
    assert c._our_relay_hints == set()


@ensureDeferred
async def test_ignore_localhost_hint_orig():
    # this actually starts the listener
    c = transit.TransitSender("")
    hints = await c.get_connection_hints()
    c._stop_listening()
    # If there are non-localhost hints, then localhost hints should be
    # removed. But if the only hint is localhost, it should stay.
    if len(hints) == 1:
        if hints[0]["hostname"] == "127.0.0.1":
            return
    for hint in hints:
        assert not (hint["hostname"] == "127.0.0.1")


@ensureDeferred
async def test_ignore_localhost_hint():
    # this actually starts the listener
    c = transit.TransitSender("")
    with mock.patch(
            "wormhole.ipaddrs.find_addresses",
            return_value=[LOOPADDR, OTHERADDR]):
        hints = await c.get_connection_hints()
    c._stop_listening()
    # If there are non-localhost hints, then localhost hints should be
    # removed.
    assert len(hints) == 1
    assert hints[0]["hostname"] == "1.2.3.4"


@ensureDeferred
async def test_keep_only_localhost_hint():
    # this actually starts the listener
    c = transit.TransitSender("")
    with mock.patch(
            "wormhole.ipaddrs.find_addresses", return_value=[LOOPADDR]):
        hints = await c.get_connection_hints()
    c._stop_listening()
    # If the only hint is localhost, it should stay.
    assert len(hints) == 1
    assert hints[0]["hostname"] == "127.0.0.1"


def test_abilities():
    c = transit.Common(None, no_listen=True)
    abilities = c.get_connection_abilities()
    assert abilities == [
        {
            "type": "direct-tcp-v1"
        },
        {
            "type": "relay-v1"
        },
    ]


@ensureDeferred
async def test_transit_key_wait():
    KEY = b"123"
    c = transit.Common("")
    d = c._get_transit_key()
    assert not d.called
    c.set_transit_key(KEY)
    assert await d == KEY


@ensureDeferred
async def test_transit_key_already_set():
    KEY = b"123"
    c = transit.Common("")
    c.set_transit_key(KEY)
    d = c._get_transit_key()
    assert await d == KEY


async def test_transit_keys():
    KEY = b"123"
    s = transit.TransitSender("")
    s.set_transit_key(KEY)
    r = transit.TransitReceiver("")
    r.set_transit_key(KEY)

    assert s._send_this() == (
        b"transit sender "
        b"559bdeae4b49fa6a23378d2b68f4c7e69378615d4af049c371c6a26e82391089"
        b" ready\n\n")
    assert s._send_this() == r._expect_this()

    assert r._send_this() == (
        b"transit receiver "
        b"ed447528194bac4c00d0c854b12a97ce51413d89aa74d6304475f516fdc23a1b"
        b" ready\n\n")
    assert r._send_this() == s._expect_this()

    assert hexlify(s._sender_record_key()) == \
        b"5a2fba3a9e524ab2e2823ff53b05f946896f6e4ce4e282ffd8e3ac0e5e9e0cda"
    assert hexlify(s._sender_record_key()) == hexlify(r._receiver_record_key())

    assert hexlify(r._sender_record_key()) == \
        b"eedb143117249f45b39da324decf6bd9aae33b7ccd58487436de611a3c6b871d"
    assert hexlify(r._sender_record_key()) == hexlify(s._receiver_record_key())


def test_connection_ready():
    s = transit.TransitSender("")
    assert s.connection_ready("p1") == "go"
    assert s._winner == "p1"
    assert s.connection_ready("p2") == "nevermind"
    assert s._winner == "p1"

    r = transit.TransitReceiver("")
    assert r.connection_ready("p1") == "wait-for-decision"
    assert r.connection_ready("p2") == "wait-for-decision"


def test_listener():
    c = transit.Common("")
    hints, ep = c._build_listener()
    assert isinstance(hints, (list, set))
    if hints:
        assert isinstance(hints[0], DirectTCPV1Hint)
    assert isinstance(ep, endpoints.TCP4ServerEndpoint)



@ensureDeferred
async def test_get_direct_hints():
    # this actually starts the listener
    c = transit.TransitSender("")

    hints = await c.get_connection_hints()

    # the hints are supposed to be cached, so calling this twice won't
    # start a second listener
    assert c._listener
    d2 = c.get_connection_hints()
    assert await d2 == hints

    c._stop_listening()


class DummyProtocol(protocol.Protocol):
    def __init__(self):
        self.buf = b""
        self._count = None
        self._d2 = None

    def wait_for(self, count):
        if len(self.buf) >= count:
            data = self.buf[:count]
            self.buf = self.buf[count:]
            return defer.succeed(data)
        self._d = defer.Deferred()
        self._count = count
        return self._d

    def dataReceived(self, data):
        self.buf += data
        # print("oDR", self._count, len(self.buf))
        if self._count is not None and len(self.buf) >= self._count:
            got = self.buf[:self._count]
            self.buf = self.buf[self._count:]
            self._count = None
            self._d.callback(got)

    def wait_for_disconnect(self):
        self._d2 = defer.Deferred()
        return self._d2

    def connectionLost(self, reason):
        if self._d2:
            self._d2.callback(None)


class FakeTransport:
    signalConnectionLost = True

    def __init__(self, p, peeraddr):
        self.protocol = p
        self._peeraddr = peeraddr
        self._buf = b""
        self._connected = True

    def write(self, data):
        self._buf += data

    def loseConnection(self):
        self._connected = False
        if self.signalConnectionLost:
            self.protocol.connectionLost()

    def getPeer(self):
        return self._peeraddr

    def read_buf(self):
        b = self._buf
        self._buf = b""
        return b


class RandomError(Exception):
    pass


class MockConnection:
    def __init__(self, owner, relay_handshake, start, description):
        self.owner = owner
        self.relay_handshake = relay_handshake
        self.start = start
        self._description = description

        def cancel(d):
            self._cancelled = True

        self._d = defer.Deferred(cancel)
        self._start_negotiation_called = False
        self._cancelled = False

    def startNegotiation(self):
        self._start_negotiation_called = True
        return self._d


def test_describe_inbound():
    f = transit.InboundConnectionFactory(None)
    addrH = address.HostnameAddress("example.com", 1234)
    assert f._describePeer(addrH) == "<-example.com:1234"
    addr4 = address.IPv4Address("TCP", "1.2.3.4", 1234)
    assert f._describePeer(addr4) == "<-1.2.3.4:1234"
    addr6 = address.IPv6Address("TCP", "::1", 1234)
    assert f._describePeer(addr6) == "<-::1:1234"
    addrU = address.UNIXAddress("/dev/unlikely")
    assert f._describePeer(addrU) == "<-UNIXAddress('/dev/unlikely')"


@ensureDeferred
async def test_success_inbound():
    f = transit.InboundConnectionFactory("owner")
    f.protocol = MockConnection
    d = f.whenDone()
    assert not d.called

    addr = address.HostnameAddress("example.com", 1234)
    p = f.buildProtocol(addr)
    assert isinstance(p, MockConnection)
    assert p.owner == "owner"
    assert p.relay_handshake is None
    assert not p._start_negotiation_called
    # meh .start

    # this is normally called from Connection.connectionMade
    f.connectionWasMade(p)
    assert p._start_negotiation_called
    assert not d.called
    assert p._description == "<-example.com:1234"

    p._d.callback(p)
    assert await d == p


@ensureDeferred
async def test_one_fail_one_success():
    f = transit.InboundConnectionFactory("owner")
    f.protocol = MockConnection
    d = f.whenDone()
    assert not d.called

    addr1 = address.HostnameAddress("example.com", 1234)
    addr2 = address.HostnameAddress("example.com", 5678)
    p1 = f.buildProtocol(addr1)
    p2 = f.buildProtocol(addr2)

    f.connectionWasMade(p1)
    f.connectionWasMade(p2)
    assert not d.called

    p1._d.errback(transit.BadHandshake("nope"))
    assert not d.called
    p2._d.callback(p2)
    assert await d == p2


@ensureDeferred
async def test_first_success_wins():
    f = transit.InboundConnectionFactory("owner")
    f.protocol = MockConnection
    d = f.whenDone()
    assert not d.called

    addr1 = address.HostnameAddress("example.com", 1234)
    addr2 = address.HostnameAddress("example.com", 5678)
    p1 = f.buildProtocol(addr1)
    p2 = f.buildProtocol(addr2)

    f.connectionWasMade(p1)
    f.connectionWasMade(p2)
    assert not d.called

    p1._d.callback(p1)
    assert await d == p1
    assert not p1._cancelled
    assert p2._cancelled


@ensureDeferred
async def test_cancel():
    f = transit.InboundConnectionFactory("owner")
    f.protocol = MockConnection
    d = f.whenDone()
    assert not d.called

    addr1 = address.HostnameAddress("example.com", 1234)
    addr2 = address.HostnameAddress("example.com", 5678)
    p1 = f.buildProtocol(addr1)
    p2 = f.buildProtocol(addr2)

    f.connectionWasMade(p1)
    f.connectionWasMade(p2)
    assert not d.called

    d.cancel()

    with pytest.raises(defer.CancelledError):
        await d
    assert p1._cancelled
    assert p2._cancelled


def test_success_outboun():
    f = transit.OutboundConnectionFactory("owner", "relay_handshake",
                                          "description")
    f.protocol = MockConnection

    addr = address.HostnameAddress("example.com", 1234)
    p = f.buildProtocol(addr)
    assert isinstance(p, MockConnection)
    assert p.owner == "owner"
    assert p.relay_handshake == "relay_handshake"
    assert not p._start_negotiation_called
    # meh .start

    # this is normally called from Connection.connectionMade
    f.connectionWasMade(p)  # no-op for outbound
    assert not p._start_negotiation_called


class MockOwner:
    _connection_ready_called = False

    def connection_ready(self, connection):
        self._connection_ready_called = True
        self._connection = connection
        return self._state

    def _send_this(self):
        return b"send_this"

    def _expect_this(self):
        return b"expect_this"

    def _sender_record_key(self):
        return b"s" * 32

    def _receiver_record_key(self):
        return b"r" * 32


class MockFactory:
    _connectionWasMade_called = False

    def connectionWasMade(self, p):
        self._connectionWasMade_called = True
        self._p = p


def test_check_and_remove():
    c = transit.Connection(None, None, None, "description")
    c.buf = b""
    EXP = b"expectation"
    assert not c._check_and_remove(EXP)
    assert c.buf == b""

    c.buf = b"unexpected"
    with pytest.raises(transit.BadHandshake) as f:
        c._check_and_remove(EXP)
    assert str(f.value) == f"got {b'unexpected'!r} want {b'expectation'!r}"
    assert c.buf == b"unexpected"

    c.buf = b"expect"
    assert not c._check_and_remove(EXP)
    assert c.buf == b"expect"

    c.buf = b"expectation"
    assert c._check_and_remove(EXP)
    assert c.buf == b""

    c.buf = b"expectation exceeded"
    assert c._check_and_remove(EXP)
    assert c.buf == b" exceeded"


def test_describe_connection():
    c = transit.Connection(None, None, None, "description")
    assert c.describe() == "description"


@ensureDeferred
async def test_sender_accepting():
    relay_handshake = None
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, relay_handshake, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    owner._state = "go"
    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called

    c.dataReceived(b"expect_this")
    assert t.read_buf() == b"go\n"
    assert t._connected
    assert c.state == "records"
    assert await d == c

    c.close()
    assert not t._connected


@ensureDeferred
async def test_sender_rejecting():
    relay_handshake = None
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, relay_handshake, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    owner._state = "nevermind"
    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called

    c.dataReceived(b"expect_this")
    assert t.read_buf() == b"nevermind\n"
    assert not t._connected
    assert c.state == "hung up"
    with pytest.raises(transit.BadHandshake) as f:
        await d
    assert str(f.value) == "abandoned"


@ensureDeferred
async def test_handshake_other_error():
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called
    c.state = RandomError("boom2")
    with pytest.raises(RandomError):
        c.dataReceived(b"surprise!")
    assert not t._connected
    assert c.state == "hung up"
    with pytest.raises(RandomError):
        await d


@ensureDeferred
async def test_handshake_bad_state():
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called
    c.state = "unknown-bogus-state"
    with pytest.raises(ValueError):
        c.dataReceived(b"surprise!")
    assert not t._connected
    assert c.state == "hung up"
    with pytest.raises(ValueError):
        await d


@ensureDeferred
async def test_relay_handshake():
    relay_handshake = b"relay handshake"
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, relay_handshake, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c
    assert t.read_buf() == b""  # quiet until startNegotiation

    owner._state = "go"
    d = c.startNegotiation()
    assert t.read_buf() == relay_handshake
    assert c.state == "relay"  # waiting for OK from relay

    c.dataReceived(b"ok\n")
    assert t.read_buf() == b"send_this"
    assert c.state == "handshake"

    assert not d.called

    c.dataReceived(b"expect_this")
    assert c.state == "records"
    assert await d == c

    assert t.read_buf() == b"go\n"


@ensureDeferred
async def test_relay_handshake_bad():
    relay_handshake = b"relay handshake"
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, relay_handshake, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c
    assert t.read_buf() == b""  # quiet until startNegotiation

    owner._state = "go"
    d = c.startNegotiation()
    assert t.read_buf() == relay_handshake
    assert c.state == "relay"  # waiting for OK from relay

    c.dataReceived(b"not ok\n")
    assert not t._connected
    assert c.state == "hung up"

    with pytest.raises(transit.BadHandshake) as f:
        await d
    assert str(f.value) == "got %r want %r" % (b"not ok\n", b"ok\n")


@ensureDeferred
async def test_receiver_accepted():
    # we're on the receiving side, so we wait for the sender to decide
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    owner._state = "wait-for-decision"
    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called

    c.dataReceived(b"expect_this")
    assert c.state == "wait-for-decision"
    assert not d.called

    c.dataReceived(b"go\n")
    assert c.state == "records"
    assert await d == c


@ensureDeferred
async def test_receiver_rejected_politely():
    # we're on the receiving side, so we wait for the sender to decide
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    owner._state = "wait-for-decision"
    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called

    c.dataReceived(b"expect_this")
    assert c.state == "wait-for-decision"
    assert not d.called

    c.dataReceived(b"nevermind\n")  # polite rejection
    assert not t._connected
    assert c.state == "hung up"
    with pytest.raises(transit.BadHandshake) as f:
        await d
    assert str(f.value) == "got %r want %r" % (b"nevermind\n", b"go\n")


@ensureDeferred
async def test_receiver_rejected_rudely():
    # we're on the receiving side, so we wait for the sender to decide
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    assert factory._connectionWasMade_called
    assert factory._p == c

    owner._state = "wait-for-decision"
    d = c.startNegotiation()
    assert c.state == "handshake"
    assert t.read_buf() == b"send_this"
    assert not d.called

    c.dataReceived(b"expect_this")
    assert c.state == "wait-for-decision"
    assert not d.called

    t.loseConnection()
    assert not t._connected
    with pytest.raises(transit.BadHandshake) as f:
        await d
    assert str(f.value) == "connection lost"


@ensureDeferred
async def test_cancel_during_negotiation():
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()

    d = c.startNegotiation()
    # while we're waiting for negotiation, we get cancelled
    d.cancel()

    assert not t._connected
    assert c.state == "hung up"
    with pytest.raises(defer.CancelledError):
        await d


@ensureDeferred
async def test_timeout():
    clock = task.Clock()
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")

    def _callLater(period, func):
        clock.callLater(period, func)

    c.callLater = _callLater
    assert c.state == "too-early"
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()
    # the timer should now be running
    d = c.startNegotiation()
    # while we're waiting for negotiation, the timer expires
    clock.advance(transit.TIMEOUT + 1.0)

    assert not t._connected
    with pytest.raises(transit.BadHandshake) as f:
        await d
    assert str(f.value) == "timeout"


async def make_connection():
    owner = MockOwner()
    factory = MockFactory()
    addr = address.HostnameAddress("example.com", 1234)
    c = transit.Connection(owner, None, None, "description")
    t = c.transport = FakeTransport(c, addr)
    c.factory = factory
    c.connectionMade()

    owner._state = "go"
    d = c.startNegotiation()
    c.dataReceived(b"expect_this")
    assert await d == c
    t.read_buf()  # flush input buffer, prepare for encrypted records

    return t, c, owner


@ensureDeferred
async def test_records_not_binary():
    t, c, owner = await make_connection()

    RECORD1 = "not binary"
    with pytest.raises(InternalError):
        c.send_record(RECORD1)


@ensureDeferred
async def test_records_good():
    # now make sure that outbound records are encrypted properly
    t, c, owner = await make_connection()

    RECORD1 = b"record"
    c.send_record(RECORD1)
    buf = t.read_buf()
    expected = f"{24 + len(RECORD1) + 16:08x}".encode("ascii")
    assert hexlify(buf[:4]) == expected
    encrypted = buf[4:]
    receive_box = SecretBox(owner._sender_record_key())
    nonce_buf = encrypted[:SecretBox.NONCE_SIZE]  # assume it's prepended
    nonce = int(hexlify(nonce_buf), 16)
    assert nonce == 0  # first message gets nonce 0
    decrypted = receive_box.decrypt(encrypted)
    assert decrypted == RECORD1

    # second message gets nonce 1
    RECORD2 = b"record2"
    c.send_record(RECORD2)
    buf = t.read_buf()
    expected = f"{24 + len(RECORD2) + 16:08x}".encode("ascii")
    assert hexlify(buf[:4]) == expected
    encrypted = buf[4:]
    receive_box = SecretBox(owner._sender_record_key())
    nonce_buf = encrypted[:SecretBox.NONCE_SIZE]  # assume it's prepended
    nonce = int(hexlify(nonce_buf), 16)
    assert nonce == 1
    decrypted = receive_box.decrypt(encrypted)
    assert decrypted == RECORD2

    # and that we can receive records properly
    inbound_records = []
    c.recordReceived = inbound_records.append
    send_box = SecretBox(owner._receiver_record_key())

    RECORD3 = b"record3"
    nonce_buf = unhexlify("%048x" % 0)  # first nonce must be 0
    encrypted = send_box.encrypt(RECORD3, nonce_buf)
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    c.dataReceived(length[:2])
    c.dataReceived(length[2:])
    c.dataReceived(encrypted[:-2])
    assert inbound_records == []
    c.dataReceived(encrypted[-2:])
    assert inbound_records == [RECORD3]

    RECORD4 = b"record4"
    nonce_buf = unhexlify("%048x" % 1)  # nonces increment
    encrypted = send_box.encrypt(RECORD4, nonce_buf)
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    c.dataReceived(length[:2])
    c.dataReceived(length[2:])
    c.dataReceived(encrypted[:-2])
    assert inbound_records == [RECORD3]
    c.dataReceived(encrypted[-2:])
    assert inbound_records == [RECORD3, RECORD4]

    # receiving two records at the same time: deliver both
    inbound_records[:] = []
    RECORD5 = b"record5"
    nonce_buf = unhexlify("%048x" % 2)  # nonces increment
    encrypted = send_box.encrypt(RECORD5, nonce_buf)
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    r5 = length + encrypted
    RECORD6 = b"record6"
    nonce_buf = unhexlify("%048x" % 3)  # nonces increment
    encrypted = send_box.encrypt(RECORD6, nonce_buf)
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    r6 = length + encrypted
    c.dataReceived(r5 + r6)
    assert inbound_records == [RECORD5, RECORD6]


def corrupt(orig):
    last_byte = orig[-1:]
    num = int(hexlify(last_byte).decode("ascii"), 16)
    corrupt_num = 256 - num
    as_byte = unhexlify(f"{corrupt_num:02x}")
    return orig[:-1] + as_byte


@ensureDeferred
async def test_records_corrupt():
    # corrupt records should be rejected
    t, c, owner = await make_connection()

    inbound_records = []
    c.recordReceived = inbound_records.append

    RECORD = b"record"
    send_box = SecretBox(owner._receiver_record_key())
    nonce_buf = unhexlify("%048x" % 0)  # first nonce must be 0
    encrypted = corrupt(send_box.encrypt(RECORD, nonce_buf))
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    c.dataReceived(length)
    c.dataReceived(encrypted[:-2])
    assert inbound_records == []
    with pytest.raises(CryptoError):
        c.dataReceived(encrypted[-2:])
    assert inbound_records == []
    # and the connection should have been dropped
    assert not t._connected


@ensureDeferred
async def test_out_of_order_nonce():
    # an inbound out-of-order nonce should be rejected
    t, c, owner = await make_connection()

    inbound_records = []
    c.recordReceived = inbound_records.append

    RECORD = b"record"
    send_box = SecretBox(owner._receiver_record_key())
    nonce_buf = unhexlify("%048x" % 1)  # first nonce must be 0
    encrypted = send_box.encrypt(RECORD, nonce_buf)
    length = unhexlify(f"{len(encrypted):08x}")  # always 4 bytes long
    c.dataReceived(length)
    c.dataReceived(encrypted[:-2])
    assert inbound_records == []
    with pytest.raises(transit.BadNonce):
        c.dataReceived(encrypted[-2:])
    assert inbound_records == []
    # and the connection should have been dropped
    assert not t._connected


# TODO: check that .connectionLost/loseConnection signatures are
# consistent: zero args, or one arg?

# XXX: if we don't set the transit key before connecting, what
# happens? We currently get a type-check assertion from HKDF because
# the key is None.


@ensureDeferred
async def test_receive_queue():
    c = transit.Connection(None, None, None, "description")
    c.transport = FakeTransport(c, None)
    c.transport.signalConnectionLost = False
    c.recordReceived(b"0")
    c.recordReceived(b"1")
    c.recordReceived(b"2")
    d0 = c.receive_record()
    assert await d0 == b"0"
    d1 = c.receive_record()
    d2 = c.receive_record()
    # they must fire in order of receipt, not order of addCallback
    assert await d2 == b"2"
    assert await d1 == b"1"

    d3 = c.receive_record()
    d4 = c.receive_record()
    assert not d3.called
    assert not d4.called

    c.recordReceived(b"3")
    assert await d3 == b"3"
    assert not d4.called

    c.recordReceived(b"4")
    assert await d4 == b"4"

    d5 = c.receive_record()
    c.close()
    with pytest.raises(error.ConnectionClosed):
        await d5


def test_producer():
    # a Transit object (receiving data from the remote peer) produces
    # data and writes it into a local Consumer
    c = transit.Connection(None, None, None, "description")
    c.transport = proto_helpers.StringTransport()
    c.recordReceived(b"r1.")
    c.recordReceived(b"r2.")

    consumer = proto_helpers.StringTransport()
    rv = c.connectConsumer(consumer)
    assert rv is None
    assert c._consumer is consumer
    assert consumer.value() == b"r1.r2."

    with pytest.raises(RuntimeError):
        c.connectConsumer(consumer)

    c.recordReceived(b"r3.")
    assert consumer.value() == b"r1.r2.r3."

    c.pauseProducing()
    assert c.transport.producerState == "paused"
    c.resumeProducing()
    assert c.transport.producerState == "producing"

    c.disconnectConsumer()
    assert consumer.producer is None
    c.connectConsumer(consumer)

    c.stopProducing()
    assert c.transport.producerState == "stopped"


@ensureDeferred
async def test_connectConsumer():
    # connectConsumer() takes an optional number of bytes to expect, and
    # fires a Deferred when that many have been written
    c = transit.Connection(None, None, None, "description")
    c._negotiation_d.addErrback(lambda err: None)  # eat it
    c.transport = proto_helpers.StringTransport()
    c.recordReceived(b"r1.")

    consumer = proto_helpers.StringTransport()
    d = c.connectConsumer(consumer, expected=10)
    assert consumer.value() == b"r1."
    assert not d.called

    c.recordReceived(b"r2.")
    assert consumer.value() == b"r1.r2."
    assert not d.called

    c.recordReceived(b"r3.")
    assert consumer.value() == b"r1.r2.r3."
    assert not d.called

    c.recordReceived(b"!")
    assert consumer.value() == b"r1.r2.r3.!"
    assert await d == 10

    # that should automatically disconnect the consumer, and subsequent
    # records should get queued, not delivered
    assert c._consumer is None
    c.recordReceived(b"overflow")
    assert consumer.value() == b"r1.r2.r3.!"

    # now test that the Deferred errbacks when the connection is lost
    d = c.connectConsumer(consumer, expected=10)

    c.connectionLost()
    with pytest.raises(error.ConnectionClosed):
        await d


@ensureDeferred
async def test_connectConsumer_empty():
    # if connectConsumer() expects 0 bytes (e.g. someone is "sending" a
    # zero-length file), make sure it gets woken up right away, so it can
    # disconnect itself, even though no bytes will actually arrive
    c = transit.Connection(None, None, None, "description")
    c._negotiation_d.addErrback(lambda err: None)  # eat it
    c.transport = proto_helpers.StringTransport()

    consumer = proto_helpers.StringTransport()
    d = c.connectConsumer(consumer, expected=0)
    assert await d == 0
    assert consumer.value() == b""
    assert c._consumer is None


@ensureDeferred
async def test_writeToFile():
    c = transit.Connection(None, None, None, "description")
    c._negotiation_d.addErrback(lambda err: None)  # eat it
    c.transport = proto_helpers.StringTransport()
    c.recordReceived(b"r1.")

    f = io.BytesIO()
    progress = []
    d = c.writeToFile(f, 10, progress.append)
    assert f.getvalue() == b"r1."
    assert progress == [3]
    assert not d.called

    c.recordReceived(b"r2.")
    assert f.getvalue() == b"r1.r2."
    assert progress == [3, 3]
    assert not d.called

    c.recordReceived(b"r3.")
    assert f.getvalue() == b"r1.r2.r3."
    assert progress == [3, 3, 3]
    assert not d.called

    c.recordReceived(b"!")
    assert f.getvalue() == b"r1.r2.r3.!"
    assert progress == [3, 3, 3, 1]
    assert await d == 10

    # that should automatically disconnect the consumer, and subsequent
    # records should get queued, not delivered
    assert c._consumer is None
    c.recordReceived(b"overflow.")
    assert f.getvalue() == b"r1.r2.r3.!"
    assert progress == [3, 3, 3, 1]

    # test what happens when enough data is queued ahead of time
    c.recordReceived(b"second.")  # now "overflow.second."
    c.recordReceived(b"third.")  # now "overflow.second.third."
    f = io.BytesIO()
    d = c.writeToFile(f, 10)
    assert f.getvalue() == b"overflow.second."  # whole records
    assert await d == 16
    assert list(c._inbound_records) == [b"third."]

    # now test that the Deferred errbacks when the connection is lost
    d = c.writeToFile(f, 10)

    c.connectionLost()
    with pytest.raises(error.ConnectionClosed):
        await d


def test_consumer():
    # a local producer sends data to a consuming Transit object
    c = transit.Connection(None, None, None, "description")
    c.transport = proto_helpers.StringTransport()
    records = []
    c.send_record = records.append

    producer = proto_helpers.StringTransport()
    c.registerProducer(producer, True)
    assert c.transport.producer is producer

    c.write(b"r1.")
    assert records == [b"r1."]

    c.unregisterProducer()
    assert c.transport.producer is None


def test_basic():
    f = io.BytesIO()
    progress = []
    fc = transit.FileConsumer(f, progress.append)
    assert progress == []
    assert f.getvalue() == b""
    fc.write(b"." * 99)
    assert progress == [99]
    assert f.getvalue() == b"." * 99
    fc.write(b"!")
    assert progress == [99, 1]
    assert f.getvalue() == b"." * 99 + b"!"


def test_hasher():
    hashee = []
    f = io.BytesIO()
    progress = []
    fc = transit.FileConsumer(f, progress.append, hasher=hashee.append)
    assert progress == []
    assert f.getvalue() == b""
    assert hashee == []
    fc.write(b"." * 99)
    assert progress == [99]
    assert f.getvalue() == b"." * 99
    assert hashee == [b"." * 99]
    fc.write(b"!")
    assert progress == [99, 1]
    assert f.getvalue() == b"." * 99 + b"!"
    assert hashee == [b"." * 99, b"!"]


DIRECT_HINT_JSON = {
    "type": "direct-tcp-v1",
    "hostname": "direct",
    "port": 1234
}
RELAY_HINT_JSON = {
    "type": "relay-v1",
    "hints": [{
        "type": "direct-tcp-v1",
        "hostname": "relay",
        "port": 1234
    }]
}
UNRECOGNIZED_DIRECT_HINT_JSON = {
    "type": "direct-tcp-v1",
    "hostname": ["cannot", "parse", "list"]
}
UNRECOGNIZED_HINT_JSON = {"type": "unknown"}
UNAVAILABLE_HINT_JSON = {
    "type": "direct-tcp-v1",  # e.g. Tor without txtorcon
    "hostname": "unavailable",
    "port": 1234
}
RELAY_HINT2_JSON = {
    "type":
    "relay-v1",
    "hints": [{
        "type": "direct-tcp-v1",
        "hostname": "relay",
        "port": 1234
    }, UNRECOGNIZED_HINT_JSON]
}
UNAVAILABLE_RELAY_HINT_JSON = {
    "type": "relay-v1",
    "hints": [UNAVAILABLE_HINT_JSON]
}


class FakeConnector:
    def __init__(self):
        self._connectors = []
        self._waiters = []
        self._descriptions = []

    def _start_connector(self, ep, description, is_relay=False):
        d = defer.Deferred()
        self._connectors.append(ep)
        self._waiters.append(d)
        self._descriptions.append(description)
        return d


@ensureDeferred
async def test_success_direct():
    reactor = mock.Mock()
    fc = FakeConnector()
    s = transit.TransitSender("", reactor=reactor)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()  # start the listener
    del hints
    s.add_connection_hints([
        DIRECT_HINT_JSON, UNRECOGNIZED_DIRECT_HINT_JSON,
        UNRECOGNIZED_HINT_JSON
    ])

    s._start_connector = fc._start_connector
    d = s.connect()
    assert not d.called
    assert len(fc._waiters) == 1
    assert isinstance(fc._waiters[0], defer.Deferred)

    fc._waiters[0].callback("winner")
    assert await d == "winner"
    assert fc._descriptions == ["->tcp:direct:1234"]


@ensureDeferred
async def test_success_direct_tor():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", tor=mock.Mock(), reactor=clock)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()  # start the listener
    del hints
    s.add_connection_hints([DIRECT_HINT_JSON])

    s._start_connector = fc._start_connector
    d = s.connect()
    assert not d.called
    assert len(fc._waiters) == 1
    assert isinstance(fc._waiters[0], defer.Deferred)

    fc._waiters[0].callback("winner")
    assert await d == "winner"
    assert fc._descriptions == ["tor->tcp:direct:1234"]


@ensureDeferred
async def test_success_direct_tor_relay():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", tor=mock.Mock(), reactor=clock)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()  # start the listener
    del hints
    s.add_connection_hints([RELAY_HINT_JSON])

    s._start_connector = fc._start_connector
    d = s.connect()
    # move the clock forward any amount, since relay connections are
    # triggered starting at T+0.0
    clock.advance(1.0)
    assert not d.called
    assert len(fc._waiters) == 1
    assert isinstance(fc._waiters[0], defer.Deferred)

    fc._waiters[0].callback("winner")
    assert await d == "winner"
    assert fc._descriptions == ["tor->relay:tcp:relay:1234"]


def _endpoint_from_hint_obj(hint, _tor, _reactor):
    if isinstance(hint, DirectTCPV1Hint):
        if hint.hostname == "unavailable":
            return None
        return hint.hostname
    return None


@ensureDeferred
async def test_wait_for_relay():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", reactor=clock, no_listen=True)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()
    del hints
    s.add_connection_hints(
        [DIRECT_HINT_JSON, UNRECOGNIZED_HINT_JSON, RELAY_HINT_JSON])
    s._start_connector = fc._start_connector

    with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                    _endpoint_from_hint_obj):
        d = s.connect()
        assert not d.called
        # the direct connectors are tried right away, but the relay
        # connectors are stalled for a few seconds
        assert fc._connectors == ["direct"]

        clock.advance(s.RELAY_DELAY + 1.0)
        assert fc._connectors == ["direct", "relay"]

        fc._waiters[0].callback("winner")
        assert await d == "winner"


@ensureDeferred
async def test_priorities():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", reactor=clock, no_listen=True)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()
    del hints
    s.add_connection_hints([
        {
            "type":
            "relay-v1",
            "hints": [{
                "type": "direct-tcp-v1",
                "hostname": "relay",
                "port": 1234
            }]
        },
        {
            "type": "direct-tcp-v1",
            "hostname": "direct",
            "port": 1234
        },
        {
            "type":
            "relay-v1",
            "hints": [{
                "type": "direct-tcp-v1",
                "priority": 2.0,
                "hostname": "relay2",
                "port": 1234
            }, {
                "type": "direct-tcp-v1",
                "priority": 3.0,
                "hostname": "relay3",
                "port": 1234
            }]
        },
        {
            "type":
            "relay-v1",
            "hints": [{
                "type": "direct-tcp-v1",
                "priority": 2.0,
                "hostname": "relay4",
                "port": 1234
            }]
        },
    ])
    s._start_connector = fc._start_connector

    with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                    _endpoint_from_hint_obj):
        d = s.connect()
        assert not d.called
        # direct connector should be used first, then the priority=3.0 relay,
        # then the two 2.0 relays, then the (default) 0.0 relay

        assert fc._connectors == ["direct"]

        clock.advance(s.RELAY_DELAY + 1.0)
        assert fc._connectors == ["direct", "relay3"]

        clock.advance(s.RELAY_DELAY)
        assert fc._connectors in \
                      (["direct", "relay3", "relay2", "relay4"],
                       ["direct", "relay3", "relay4", "relay2"])

        clock.advance(s.RELAY_DELAY)
        assert fc._connectors in \
                      (["direct", "relay3", "relay2", "relay4", "relay"],
                       ["direct", "relay3", "relay4", "relay2", "relay"])

        fc._waiters[0].callback("winner")
        assert await d == "winner"


@ensureDeferred
async def test_no_direct_hints():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", reactor=clock, no_listen=True)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()  # start the listener
    del hints
    # include hints that can't be turned into an endpoint at runtime
    s.add_connection_hints([
        UNRECOGNIZED_HINT_JSON, UNAVAILABLE_HINT_JSON, RELAY_HINT2_JSON,
        UNAVAILABLE_RELAY_HINT_JSON
    ])
    s._start_connector = fc._start_connector

    with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                    _endpoint_from_hint_obj):
        d = s.connect()
        assert not d.called
        # since there are no usable direct hints, the relay connector will
        # only be stalled for 0 seconds
        assert fc._connectors == []

        clock.advance(0)
        assert fc._connectors == ["relay"]

        fc._waiters[0].callback("winner")
        assert await d == "winner"


@ensureDeferred
async def test_no_contenders():
    clock = task.Clock()
    fc = FakeConnector()
    s = transit.TransitSender("", reactor=clock, no_listen=True)
    s.set_transit_key(b"key")
    hints = await s.get_connection_hints()  # start the listener
    del hints
    s.add_connection_hints([])  # no hints at all
    s._start_connector = fc._start_connector

    with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                    _endpoint_from_hint_obj):
        d = s.connect()
        with pytest.raises(transit.TransitError) as f:
            await d
        assert str(f.value) == "No contenders for connection"


def old_build_relay_handshake(key):
    token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
    return (token, b"please relay " + hexlify(token) + b"\n")



def test_old():
    key = b"\x00"
    token, old_handshake = old_build_relay_handshake(key)
    tc = transit_server.TransitConnection()
    tc.factory = mock.Mock()
    tc.factory.connection_got_token = mock.Mock()
    tc.transport = mock.Mock()
    tc.connectionMade()
    tc._state.please_relay = mock.Mock()
    tc._state.please_relay_for_side = mock.Mock()
    tc.dataReceived(old_handshake[:-1])
    assert tc._state.please_relay.mock_calls == []
    tc.dataReceived(old_handshake[-1:])
    assert tc._state.please_relay.mock_calls == \
                     [mock.call(hexlify(token))]
    assert tc._state.please_relay_for_side.mock_calls == []


def test_new():
    c = transit.Common(None)
    c.set_transit_key(b"\x00")
    new_handshake = c._build_relay_handshake()
    token, old_handshake = old_build_relay_handshake(b"\x00")

    tc = transit_server.TransitConnection()
    tc.factory = mock.Mock()
    tc.transport = mock.Mock()
    tc.connectionMade()
    tc._state._real_register_token_for_side = m = mock.Mock()
    tc.dataReceived(new_handshake[:-1])
    assert m.mock_calls == []
    tc.dataReceived(new_handshake[-1:])
    assert m.mock_calls == \
        [mock.call(hexlify(token), c._side.encode("ascii"))]


async def doBoth(d1, d2):
    return await gatherResults([d1, d2], True)


@ensureDeferred
async def test_direct():
    KEY = b"k" * 32
    s = transit.TransitSender(None)
    r = transit.TransitReceiver(None)

    s.set_transit_key(KEY)
    r.set_transit_key(KEY)

    # TODO: this sometimes fails with EADDRINUSE
    shints = await s.get_connection_hints()
    rhints = await r.get_connection_hints()

    s.add_connection_hints(rhints)
    r.add_connection_hints(shints)

    (x, y) = await doBoth(s.connect(), r.connect())
    assert isinstance(x, transit.Connection)
    assert isinstance(y, transit.Connection)

    d = y.receive_record()

    x.send_record(b"record1")
    r = await d
    assert r == b"record1"

    x.close()
    y.close()


@ensureDeferred
async def test_relay(transit_relay):
    KEY = b"k" * 32
    s = transit.TransitSender(transit_relay, no_listen=True)
    r = transit.TransitReceiver(transit_relay, no_listen=True)

    s.set_transit_key(KEY)
    r.set_transit_key(KEY)

    shints = await s.get_connection_hints()
    rhints = await r.get_connection_hints()

    s.add_connection_hints(rhints)
    r.add_connection_hints(shints)

    (x, y) = await doBoth(s.connect(), r.connect())
    assert isinstance(x, transit.Connection)
    assert isinstance(y, transit.Connection)

    d = y.receive_record()

    x.send_record(b"record1")
    r = await d
    assert r == b"record1"

    x.close()
    y.close()
