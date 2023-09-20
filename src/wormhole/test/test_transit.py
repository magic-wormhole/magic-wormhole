from __future__ import print_function, unicode_literals

import gc
import io
from binascii import hexlify, unhexlify

import six
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from twisted.internet import address, defer, endpoints, error, protocol, task
from twisted.internet.defer import gatherResults, inlineCallbacks
from twisted.python import log
from twisted.test import proto_helpers
from twisted.trial import unittest

from unittest import mock
from wormhole_transit_relay import transit_server

from .. import transit
from .._hints import DirectTCPV1Hint
from ..errors import InternalError
from ..util import HKDF
from .common import ServerBase


class Highlander(unittest.TestCase):
    def test_one_winner(self):
        cancelled = set()
        contenders = [
            defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(5)
        ]
        d = transit.there_can_be_only_one(contenders)
        self.assertNoResult(d)
        contenders[0].errback(ValueError())
        self.assertNoResult(d)
        contenders[1].errback(TypeError())
        self.assertNoResult(d)
        contenders[2].callback("yay")
        self.assertEqual(self.successResultOf(d), "yay")
        self.assertEqual(cancelled, set([3, 4]))

    def test_there_might_also_be_none(self):
        cancelled = set()
        contenders = [
            defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
        ]
        d = transit.there_can_be_only_one(contenders)
        self.assertNoResult(d)
        contenders[0].errback(ValueError())
        self.assertNoResult(d)
        contenders[1].errback(TypeError())
        self.assertNoResult(d)
        contenders[2].errback(TypeError())
        self.assertNoResult(d)
        contenders[3].errback(NameError())
        self.failureResultOf(d, ValueError)  # first failure is recorded
        self.assertEqual(cancelled, set())

    def test_cancel_early(self):
        cancelled = set()
        contenders = [
            defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
        ]
        d = transit.there_can_be_only_one(contenders)
        self.assertNoResult(d)
        self.assertEqual(cancelled, set())
        d.cancel()
        self.failureResultOf(d, defer.CancelledError)
        self.assertEqual(cancelled, set(range(4)))

    def test_cancel_after_one_failure(self):
        cancelled = set()
        contenders = [
            defer.Deferred(lambda d, i=i: cancelled.add(i)) for i in range(4)
        ]
        d = transit.there_can_be_only_one(contenders)
        self.assertNoResult(d)
        self.assertEqual(cancelled, set())
        contenders[0].errback(ValueError())
        d.cancel()
        self.failureResultOf(d, ValueError)
        self.assertEqual(cancelled, set([1, 2, 3]))


class Forever(unittest.TestCase):
    def _forever_setup(self):
        clock = task.Clock()
        c = transit.Common("", reactor=clock)
        cancelled = []
        d0 = defer.Deferred(cancelled.append)
        d = c._not_forever(1.0, d0)
        return c, clock, d0, d, cancelled

    def test_not_forever_fires(self):
        c, clock, d0, d, cancelled = self._forever_setup()
        self.assertNoResult(d)
        self.assertEqual(cancelled, [])
        d.callback(1)
        self.assertEqual(self.successResultOf(d), 1)
        self.assertEqual(cancelled, [])
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_errs(self):
        c, clock, d0, d, cancelled = self._forever_setup()
        self.assertNoResult(d)
        self.assertEqual(cancelled, [])
        d.errback(ValueError())
        self.assertEqual(cancelled, [])
        self.failureResultOf(d, ValueError)
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_cancel_early(self):
        c, clock, d0, d, cancelled = self._forever_setup()
        self.assertNoResult(d)
        self.assertEqual(cancelled, [])
        d.cancel()
        self.assertEqual(cancelled, [d0])
        self.failureResultOf(d, defer.CancelledError)
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_timeout(self):
        c, clock, d0, d, cancelled = self._forever_setup()
        self.assertNoResult(d)
        self.assertEqual(cancelled, [])
        clock.advance(2.0)
        self.assertEqual(cancelled, [d0])
        self.failureResultOf(d, defer.CancelledError)
        self.assertNot(clock.getDelayedCalls())


class Misc(unittest.TestCase):
    def test_allocate_port(self):
        portno = transit.allocate_tcp_port()
        self.assertIsInstance(portno, int)

    def test_allocate_port_no_reuseaddr(self):
        mock_sys = mock.Mock()
        mock_sys.platform = "cygwin"
        with mock.patch("wormhole.transit.sys", mock_sys):
            portno = transit.allocate_tcp_port()
        self.assertIsInstance(portno, int)


# ipaddrs.py currently uses native strings: bytes on py2, unicode on
# py3
if six.PY2:
    LOOPADDR = b"127.0.0.1"
    OTHERADDR = b"1.2.3.4"
else:
    LOOPADDR = "127.0.0.1"  # unicode_literals
    OTHERADDR = "1.2.3.4"


class Basic(unittest.TestCase):
    @inlineCallbacks
    def test_relay_hints(self):
        URL = "tcp:host:1234"
        c = transit.Common(URL, no_listen=True)
        hints = yield c.get_connection_hints()
        self.assertEqual(hints, [{
            "type":
            "relay-v1",
            "hints": [{
                "type": "direct-tcp-v1",
                "hostname": "host",
                "port": 1234,
                "priority": 0.0
            }],
        }])
        self.assertRaises(InternalError, transit.Common, 123)

    @inlineCallbacks
    def test_no_relay_hints(self):
        c = transit.Common(None, no_listen=True)
        hints = yield c.get_connection_hints()
        self.assertEqual(hints, [])

    def test_ignore_bad_hints(self):
        c = transit.Common("")
        c.add_connection_hints([{"type": "unknown"}])
        c.add_connection_hints([{
            "type": "relay-v1",
            "hints": [{
                "type": "unknown"
            }]
        }])
        self.assertEqual(c._their_direct_hints, [])
        self.assertEqual(c._our_relay_hints, set())

    def test_ignore_localhost_hint_orig(self):
        # this actually starts the listener
        c = transit.TransitSender("")
        hints = self.successResultOf(c.get_connection_hints())
        c._stop_listening()
        # If there are non-localhost hints, then localhost hints should be
        # removed. But if the only hint is localhost, it should stay.
        if len(hints) == 1:
            if hints[0]["hostname"] == "127.0.0.1":
                return
        for hint in hints:
            self.assertFalse(hint["hostname"] == "127.0.0.1")

    def test_ignore_localhost_hint(self):
        # this actually starts the listener
        c = transit.TransitSender("")
        with mock.patch(
                "wormhole.ipaddrs.find_addresses",
                return_value=[LOOPADDR, OTHERADDR]):
            hints = self.successResultOf(c.get_connection_hints())
        c._stop_listening()
        # If there are non-localhost hints, then localhost hints should be
        # removed.
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["hostname"], "1.2.3.4")

    def test_keep_only_localhost_hint(self):
        # this actually starts the listener
        c = transit.TransitSender("")
        with mock.patch(
                "wormhole.ipaddrs.find_addresses", return_value=[LOOPADDR]):
            hints = self.successResultOf(c.get_connection_hints())
        c._stop_listening()
        # If the only hint is localhost, it should stay.
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["hostname"], "127.0.0.1")

    def test_abilities(self):
        c = transit.Common(None, no_listen=True)
        abilities = c.get_connection_abilities()
        self.assertEqual(abilities, [
            {
                "type": "direct-tcp-v1"
            },
            {
                "type": "relay-v1"
            },
        ])

    def test_transit_key_wait(self):
        KEY = b"123"
        c = transit.Common("")
        d = c._get_transit_key()
        self.assertNoResult(d)
        c.set_transit_key(KEY)
        self.assertEqual(self.successResultOf(d), KEY)

    def test_transit_key_already_set(self):
        KEY = b"123"
        c = transit.Common("")
        c.set_transit_key(KEY)
        d = c._get_transit_key()
        self.assertEqual(self.successResultOf(d), KEY)

    def test_transit_keys(self):
        KEY = b"123"
        s = transit.TransitSender("")
        s.set_transit_key(KEY)
        r = transit.TransitReceiver("")
        r.set_transit_key(KEY)

        self.assertEqual(s._send_this(), (
            b"transit sender "
            b"559bdeae4b49fa6a23378d2b68f4c7e69378615d4af049c371c6a26e82391089"
            b" ready\n\n"))
        self.assertEqual(s._send_this(), r._expect_this())

        self.assertEqual(r._send_this(), (
            b"transit receiver "
            b"ed447528194bac4c00d0c854b12a97ce51413d89aa74d6304475f516fdc23a1b"
            b" ready\n\n"))
        self.assertEqual(r._send_this(), s._expect_this())

        self.assertEqual(
            hexlify(s._sender_record_key()),
            b"5a2fba3a9e524ab2e2823ff53b05f946896f6e4ce4e282ffd8e3ac0e5e9e0cda"
        )
        self.assertEqual(
            hexlify(s._sender_record_key()), hexlify(r._receiver_record_key()))

        self.assertEqual(
            hexlify(r._sender_record_key()),
            b"eedb143117249f45b39da324decf6bd9aae33b7ccd58487436de611a3c6b871d"
        )
        self.assertEqual(
            hexlify(r._sender_record_key()), hexlify(s._receiver_record_key()))

    def test_connection_ready(self):
        s = transit.TransitSender("")
        self.assertEqual(s.connection_ready("p1"), "go")
        self.assertEqual(s._winner, "p1")
        self.assertEqual(s.connection_ready("p2"), "nevermind")
        self.assertEqual(s._winner, "p1")

        r = transit.TransitReceiver("")
        self.assertEqual(r.connection_ready("p1"), "wait-for-decision")
        self.assertEqual(r.connection_ready("p2"), "wait-for-decision")


class Listener(unittest.TestCase):
    def test_listener(self):
        c = transit.Common("")
        hints, ep = c._build_listener()
        self.assertIsInstance(hints, (list, set))
        if hints:
            self.assertIsInstance(hints[0], DirectTCPV1Hint)
        self.assertIsInstance(ep, endpoints.TCP4ServerEndpoint)

    def test_get_direct_hints(self):
        # this actually starts the listener
        c = transit.TransitSender("")

        d = c.get_connection_hints()
        hints = self.successResultOf(d)

        # the hints are supposed to be cached, so calling this twice won't
        # start a second listener
        self.assert_(c._listener)
        d2 = c.get_connection_hints()
        self.assertEqual(self.successResultOf(d2), hints)

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


class InboundConnectionFactory(unittest.TestCase):
    def test_describe(self):
        f = transit.InboundConnectionFactory(None)
        addrH = address.HostnameAddress("example.com", 1234)
        self.assertEqual(f._describePeer(addrH), "<-example.com:1234")
        addr4 = address.IPv4Address("TCP", "1.2.3.4", 1234)
        self.assertEqual(f._describePeer(addr4), "<-1.2.3.4:1234")
        addr6 = address.IPv6Address("TCP", "::1", 1234)
        self.assertEqual(f._describePeer(addr6), "<-::1:1234")
        addrU = address.UNIXAddress("/dev/unlikely")
        self.assertEqual(
            f._describePeer(addrU), "<-UNIXAddress('/dev/unlikely')")

    def test_success(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        d = f.whenDone()
        self.assertNoResult(d)

        addr = address.HostnameAddress("example.com", 1234)
        p = f.buildProtocol(addr)
        self.assertIsInstance(p, MockConnection)
        self.assertEqual(p.owner, "owner")
        self.assertEqual(p.relay_handshake, None)
        self.assertEqual(p._start_negotiation_called, False)
        # meh .start

        # this is normally called from Connection.connectionMade
        f.connectionWasMade(p)
        self.assertEqual(p._start_negotiation_called, True)
        self.assertNoResult(d)
        self.assertEqual(p._description, "<-example.com:1234")

        p._d.callback(p)
        self.assertEqual(self.successResultOf(d), p)

    def test_one_fail_one_success(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        d = f.whenDone()
        self.assertNoResult(d)

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertNoResult(d)

        p1._d.errback(transit.BadHandshake("nope"))
        self.assertNoResult(d)
        p2._d.callback(p2)
        self.assertEqual(self.successResultOf(d), p2)

    def test_first_success_wins(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        d = f.whenDone()
        self.assertNoResult(d)

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertNoResult(d)

        p1._d.callback(p1)
        self.assertEqual(self.successResultOf(d), p1)
        self.assertEqual(p1._cancelled, False)
        self.assertEqual(p2._cancelled, True)

    def test_log_other_errors(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        d = f.whenDone()
        self.assertNoResult(d)

        addr = address.HostnameAddress("example.com", 1234)
        p1 = f.buildProtocol(addr)

        # if the Connection protocol throws an unexpected error, that should
        # get logged to the Twisted logs (as an Unhandled Error in Deferred)
        # so we can diagnose the bug
        f.connectionWasMade(p1)
        our_error = RandomError("boom1")
        p1._d.errback(our_error)
        self.assertNoResult(d)

        log.msg("=== note: the next RandomError is expected ===")
        # Make sure the Deferred has gone out of scope, so the UnhandledError
        # happens quickly. We must manually break the gc cycle.
        del p1._d
        gc.collect()  # make PyPy happy
        errors = self.flushLoggedErrors(RandomError)
        self.assertEqual(1, len(errors))
        self.assertEqual(our_error, errors[0].value)
        log.msg("=== note: the preceding RandomError was expected ===")

    def test_cancel(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        d = f.whenDone()
        self.assertNoResult(d)

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertNoResult(d)

        d.cancel()

        self.failureResultOf(d, defer.CancelledError)
        self.assertEqual(p1._cancelled, True)
        self.assertEqual(p2._cancelled, True)


# XXX check descriptions


class OutboundConnectionFactory(unittest.TestCase):
    def test_success(self):
        f = transit.OutboundConnectionFactory("owner", "relay_handshake",
                                              "description")
        f.protocol = MockConnection

        addr = address.HostnameAddress("example.com", 1234)
        p = f.buildProtocol(addr)
        self.assertIsInstance(p, MockConnection)
        self.assertEqual(p.owner, "owner")
        self.assertEqual(p.relay_handshake, "relay_handshake")
        self.assertEqual(p._start_negotiation_called, False)
        # meh .start

        # this is normally called from Connection.connectionMade
        f.connectionWasMade(p)  # no-op for outbound
        self.assertEqual(p._start_negotiation_called, False)


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


class Connection(unittest.TestCase):
    # exercise the Connection protocol class

    def test_check_and_remove(self):
        c = transit.Connection(None, None, None, "description")
        c.buf = b""
        EXP = b"expectation"
        self.assertFalse(c._check_and_remove(EXP))
        self.assertEqual(c.buf, b"")

        c.buf = b"unexpected"
        e = self.assertRaises(transit.BadHandshake, c._check_and_remove, EXP)
        self.assertEqual(
            str(e), "got %r want %r" % (b'unexpected', b'expectation'))
        self.assertEqual(c.buf, b"unexpected")

        c.buf = b"expect"
        self.assertFalse(c._check_and_remove(EXP))
        self.assertEqual(c.buf, b"expect")

        c.buf = b"expectation"
        self.assertTrue(c._check_and_remove(EXP))
        self.assertEqual(c.buf, b"")

        c.buf = b"expectation exceeded"
        self.assertTrue(c._check_and_remove(EXP))
        self.assertEqual(c.buf, b" exceeded")

    def test_describe(self):
        c = transit.Connection(None, None, None, "description")
        self.assertEqual(c.describe(), "description")

    def test_sender_accepting(self):
        relay_handshake = None
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, relay_handshake, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        owner._state = "go"
        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(t.read_buf(), b"go\n")
        self.assertEqual(t._connected, True)
        self.assertEqual(c.state, "records")
        self.assertEqual(self.successResultOf(d), c)

        c.close()
        self.assertEqual(t._connected, False)

    def test_sender_rejecting(self):
        relay_handshake = None
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, relay_handshake, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        owner._state = "nevermind"
        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(t.read_buf(), b"nevermind\n")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        f = self.failureResultOf(d, transit.BadHandshake)
        self.assertEqual(str(f.value), "abandoned")

    def test_handshake_other_error(self):
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)
        c.state = RandomError("boom2")
        self.assertRaises(RandomError, c.dataReceived, b"surprise!")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.failureResultOf(d, RandomError)

    def test_handshake_bad_state(self):
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)
        c.state = "unknown-bogus-state"
        self.assertRaises(ValueError, c.dataReceived, b"surprise!")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.failureResultOf(d, ValueError)

    def test_relay_handshake(self):
        relay_handshake = b"relay handshake"
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, relay_handshake, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)
        self.assertEqual(t.read_buf(), b"")  # quiet until startNegotiation

        owner._state = "go"
        d = c.startNegotiation()
        self.assertEqual(t.read_buf(), relay_handshake)
        self.assertEqual(c.state, "relay")  # waiting for OK from relay

        c.dataReceived(b"ok\n")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertEqual(c.state, "handshake")

        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "records")
        self.assertEqual(self.successResultOf(d), c)

        self.assertEqual(t.read_buf(), b"go\n")

    def test_relay_handshake_bad(self):
        relay_handshake = b"relay handshake"
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, relay_handshake, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)
        self.assertEqual(t.read_buf(), b"")  # quiet until startNegotiation

        owner._state = "go"
        d = c.startNegotiation()
        self.assertEqual(t.read_buf(), relay_handshake)
        self.assertEqual(c.state, "relay")  # waiting for OK from relay

        c.dataReceived(b"not ok\n")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")

        f = self.failureResultOf(d, transit.BadHandshake)
        self.assertEqual(
            str(f.value), "got %r want %r" % (b"not ok\n", b"ok\n"))

    def test_receiver_accepted(self):
        # we're on the receiving side, so we wait for the sender to decide
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        owner._state = "wait-for-decision"
        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertNoResult(d)

        c.dataReceived(b"go\n")
        self.assertEqual(c.state, "records")
        self.assertEqual(self.successResultOf(d), c)

    def test_receiver_rejected_politely(self):
        # we're on the receiving side, so we wait for the sender to decide
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        owner._state = "wait-for-decision"
        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertNoResult(d)

        c.dataReceived(b"nevermind\n")  # polite rejection
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        f = self.failureResultOf(d, transit.BadHandshake)
        self.assertEqual(
            str(f.value), "got %r want %r" % (b"nevermind\n", b"go\n"))

    def test_receiver_rejected_rudely(self):
        # we're on the receiving side, so we wait for the sender to decide
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        self.assertEqual(factory._connectionWasMade_called, True)
        self.assertEqual(factory._p, c)

        owner._state = "wait-for-decision"
        d = c.startNegotiation()
        self.assertEqual(c.state, "handshake")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertNoResult(d)

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertNoResult(d)

        t.loseConnection()
        self.assertEqual(t._connected, False)
        f = self.failureResultOf(d, transit.BadHandshake)
        self.assertEqual(str(f.value), "connection lost")

    def test_cancel(self):
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()

        d = c.startNegotiation()
        # while we're waiting for negotiation, we get cancelled
        d.cancel()

        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.failureResultOf(d, defer.CancelledError)

    def test_timeout(self):
        clock = task.Clock()
        owner = MockOwner()
        factory = MockFactory()
        addr = address.HostnameAddress("example.com", 1234)
        c = transit.Connection(owner, None, None, "description")

        def _callLater(period, func):
            clock.callLater(period, func)

        c.callLater = _callLater
        self.assertEqual(c.state, "too-early")
        t = c.transport = FakeTransport(c, addr)
        c.factory = factory
        c.connectionMade()
        # the timer should now be running
        d = c.startNegotiation()
        # while we're waiting for negotiation, the timer expires
        clock.advance(transit.TIMEOUT + 1.0)

        self.assertEqual(t._connected, False)
        f = self.failureResultOf(d, transit.BadHandshake)
        self.assertEqual(str(f.value), "timeout")

    def make_connection(self):
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
        self.assertEqual(self.successResultOf(d), c)
        t.read_buf()  # flush input buffer, prepare for encrypted records

        return t, c, owner

    def test_records_not_binary(self):
        t, c, owner = self.make_connection()

        RECORD1 = u"not binary"
        with self.assertRaises(InternalError):
            c.send_record(RECORD1)

    def test_records_good(self):
        # now make sure that outbound records are encrypted properly
        t, c, owner = self.make_connection()

        RECORD1 = b"record"
        c.send_record(RECORD1)
        buf = t.read_buf()
        expected = ("%08x" % (24 + len(RECORD1) + 16)).encode("ascii")
        self.assertEqual(hexlify(buf[:4]), expected)
        encrypted = buf[4:]
        receive_box = SecretBox(owner._sender_record_key())
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE]  # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        self.assertEqual(nonce, 0)  # first message gets nonce 0
        decrypted = receive_box.decrypt(encrypted)
        self.assertEqual(decrypted, RECORD1)

        # second message gets nonce 1
        RECORD2 = b"record2"
        c.send_record(RECORD2)
        buf = t.read_buf()
        expected = ("%08x" % (24 + len(RECORD2) + 16)).encode("ascii")
        self.assertEqual(hexlify(buf[:4]), expected)
        encrypted = buf[4:]
        receive_box = SecretBox(owner._sender_record_key())
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE]  # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        self.assertEqual(nonce, 1)
        decrypted = receive_box.decrypt(encrypted)
        self.assertEqual(decrypted, RECORD2)

        # and that we can receive records properly
        inbound_records = []
        c.recordReceived = inbound_records.append
        send_box = SecretBox(owner._receiver_record_key())

        RECORD3 = b"record3"
        nonce_buf = unhexlify("%048x" % 0)  # first nonce must be 0
        encrypted = send_box.encrypt(RECORD3, nonce_buf)
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        c.dataReceived(length[:2])
        c.dataReceived(length[2:])
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [])
        c.dataReceived(encrypted[-2:])
        self.assertEqual(inbound_records, [RECORD3])

        RECORD4 = b"record4"
        nonce_buf = unhexlify("%048x" % 1)  # nonces increment
        encrypted = send_box.encrypt(RECORD4, nonce_buf)
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        c.dataReceived(length[:2])
        c.dataReceived(length[2:])
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [RECORD3])
        c.dataReceived(encrypted[-2:])
        self.assertEqual(inbound_records, [RECORD3, RECORD4])

        # receiving two records at the same time: deliver both
        inbound_records[:] = []
        RECORD5 = b"record5"
        nonce_buf = unhexlify("%048x" % 2)  # nonces increment
        encrypted = send_box.encrypt(RECORD5, nonce_buf)
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        r5 = length + encrypted
        RECORD6 = b"record6"
        nonce_buf = unhexlify("%048x" % 3)  # nonces increment
        encrypted = send_box.encrypt(RECORD6, nonce_buf)
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        r6 = length + encrypted
        c.dataReceived(r5 + r6)
        self.assertEqual(inbound_records, [RECORD5, RECORD6])

    def corrupt(self, orig):
        last_byte = orig[-1:]
        num = int(hexlify(last_byte).decode("ascii"), 16)
        corrupt_num = 256 - num
        as_byte = unhexlify("%02x" % corrupt_num)
        return orig[:-1] + as_byte

    def test_records_corrupt(self):
        # corrupt records should be rejected
        t, c, owner = self.make_connection()

        inbound_records = []
        c.recordReceived = inbound_records.append

        RECORD = b"record"
        send_box = SecretBox(owner._receiver_record_key())
        nonce_buf = unhexlify("%048x" % 0)  # first nonce must be 0
        encrypted = self.corrupt(send_box.encrypt(RECORD, nonce_buf))
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        c.dataReceived(length)
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [])
        self.assertRaises(CryptoError, c.dataReceived, encrypted[-2:])
        self.assertEqual(inbound_records, [])
        # and the connection should have been dropped
        self.assertEqual(t._connected, False)

    def test_out_of_order_nonce(self):
        # an inbound out-of-order nonce should be rejected
        t, c, owner = self.make_connection()

        inbound_records = []
        c.recordReceived = inbound_records.append

        RECORD = b"record"
        send_box = SecretBox(owner._receiver_record_key())
        nonce_buf = unhexlify("%048x" % 1)  # first nonce must be 0
        encrypted = send_box.encrypt(RECORD, nonce_buf)
        length = unhexlify("%08x" % len(encrypted))  # always 4 bytes long
        c.dataReceived(length)
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [])
        self.assertRaises(transit.BadNonce, c.dataReceived, encrypted[-2:])
        self.assertEqual(inbound_records, [])
        # and the connection should have been dropped
        self.assertEqual(t._connected, False)

        # TODO: check that .connectionLost/loseConnection signatures are
        # consistent: zero args, or one arg?

        # XXX: if we don't set the transit key before connecting, what
        # happens? We currently get a type-check assertion from HKDF because
        # the key is None.

    def test_receive_queue(self):
        c = transit.Connection(None, None, None, "description")
        c.transport = FakeTransport(c, None)
        c.transport.signalConnectionLost = False
        c.recordReceived(b"0")
        c.recordReceived(b"1")
        c.recordReceived(b"2")
        d0 = c.receive_record()
        self.assertEqual(self.successResultOf(d0), b"0")
        d1 = c.receive_record()
        d2 = c.receive_record()
        # they must fire in order of receipt, not order of addCallback
        self.assertEqual(self.successResultOf(d2), b"2")
        self.assertEqual(self.successResultOf(d1), b"1")

        d3 = c.receive_record()
        d4 = c.receive_record()
        self.assertNoResult(d3)
        self.assertNoResult(d4)

        c.recordReceived(b"3")
        self.assertEqual(self.successResultOf(d3), b"3")
        self.assertNoResult(d4)

        c.recordReceived(b"4")
        self.assertEqual(self.successResultOf(d4), b"4")

        d5 = c.receive_record()
        c.close()
        self.failureResultOf(d5, error.ConnectionClosed)

    def test_producer(self):
        # a Transit object (receiving data from the remote peer) produces
        # data and writes it into a local Consumer
        c = transit.Connection(None, None, None, "description")
        c.transport = proto_helpers.StringTransport()
        c.recordReceived(b"r1.")
        c.recordReceived(b"r2.")

        consumer = proto_helpers.StringTransport()
        rv = c.connectConsumer(consumer)
        self.assertIs(rv, None)
        self.assertIs(c._consumer, consumer)
        self.assertEqual(consumer.value(), b"r1.r2.")

        self.assertRaises(RuntimeError, c.connectConsumer, consumer)

        c.recordReceived(b"r3.")
        self.assertEqual(consumer.value(), b"r1.r2.r3.")

        c.pauseProducing()
        self.assertEqual(c.transport.producerState, "paused")
        c.resumeProducing()
        self.assertEqual(c.transport.producerState, "producing")

        c.disconnectConsumer()
        self.assertEqual(consumer.producer, None)
        c.connectConsumer(consumer)

        c.stopProducing()
        self.assertEqual(c.transport.producerState, "stopped")

    def test_connectConsumer(self):
        # connectConsumer() takes an optional number of bytes to expect, and
        # fires a Deferred when that many have been written
        c = transit.Connection(None, None, None, "description")
        c._negotiation_d.addErrback(lambda err: None)  # eat it
        c.transport = proto_helpers.StringTransport()
        c.recordReceived(b"r1.")

        consumer = proto_helpers.StringTransport()
        d = c.connectConsumer(consumer, expected=10)
        self.assertEqual(consumer.value(), b"r1.")
        self.assertNoResult(d)

        c.recordReceived(b"r2.")
        self.assertEqual(consumer.value(), b"r1.r2.")
        self.assertNoResult(d)

        c.recordReceived(b"r3.")
        self.assertEqual(consumer.value(), b"r1.r2.r3.")
        self.assertNoResult(d)

        c.recordReceived(b"!")
        self.assertEqual(consumer.value(), b"r1.r2.r3.!")
        self.assertEqual(self.successResultOf(d), 10)

        # that should automatically disconnect the consumer, and subsequent
        # records should get queued, not delivered
        self.assertIs(c._consumer, None)
        c.recordReceived(b"overflow")
        self.assertEqual(consumer.value(), b"r1.r2.r3.!")

        # now test that the Deferred errbacks when the connection is lost
        d = c.connectConsumer(consumer, expected=10)

        c.connectionLost()
        self.failureResultOf(d, error.ConnectionClosed)

    def test_connectConsumer_empty(self):
        # if connectConsumer() expects 0 bytes (e.g. someone is "sending" a
        # zero-length file), make sure it gets woken up right away, so it can
        # disconnect itself, even though no bytes will actually arrive
        c = transit.Connection(None, None, None, "description")
        c._negotiation_d.addErrback(lambda err: None)  # eat it
        c.transport = proto_helpers.StringTransport()

        consumer = proto_helpers.StringTransport()
        d = c.connectConsumer(consumer, expected=0)
        self.assertEqual(self.successResultOf(d), 0)
        self.assertEqual(consumer.value(), b"")
        self.assertIs(c._consumer, None)

    def test_writeToFile(self):
        c = transit.Connection(None, None, None, "description")
        c._negotiation_d.addErrback(lambda err: None)  # eat it
        c.transport = proto_helpers.StringTransport()
        c.recordReceived(b"r1.")

        f = io.BytesIO()
        progress = []
        d = c.writeToFile(f, 10, progress.append)
        self.assertEqual(f.getvalue(), b"r1.")
        self.assertEqual(progress, [3])
        self.assertNoResult(d)

        c.recordReceived(b"r2.")
        self.assertEqual(f.getvalue(), b"r1.r2.")
        self.assertEqual(progress, [3, 3])
        self.assertNoResult(d)

        c.recordReceived(b"r3.")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.")
        self.assertEqual(progress, [3, 3, 3])
        self.assertNoResult(d)

        c.recordReceived(b"!")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.!")
        self.assertEqual(progress, [3, 3, 3, 1])
        self.assertEqual(self.successResultOf(d), 10)

        # that should automatically disconnect the consumer, and subsequent
        # records should get queued, not delivered
        self.assertIs(c._consumer, None)
        c.recordReceived(b"overflow.")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.!")
        self.assertEqual(progress, [3, 3, 3, 1])

        # test what happens when enough data is queued ahead of time
        c.recordReceived(b"second.")  # now "overflow.second."
        c.recordReceived(b"third.")  # now "overflow.second.third."
        f = io.BytesIO()
        d = c.writeToFile(f, 10)
        self.assertEqual(f.getvalue(), b"overflow.second.")  # whole records
        self.assertEqual(self.successResultOf(d), 16)
        self.assertEqual(list(c._inbound_records), [b"third."])

        # now test that the Deferred errbacks when the connection is lost
        d = c.writeToFile(f, 10)

        c.connectionLost()
        self.failureResultOf(d, error.ConnectionClosed)

    def test_consumer(self):
        # a local producer sends data to a consuming Transit object
        c = transit.Connection(None, None, None, "description")
        c.transport = proto_helpers.StringTransport()
        records = []
        c.send_record = records.append

        producer = proto_helpers.StringTransport()
        c.registerProducer(producer, True)
        self.assertIs(c.transport.producer, producer)

        c.write(b"r1.")
        self.assertEqual(records, [b"r1."])

        c.unregisterProducer()
        self.assertEqual(c.transport.producer, None)


class FileConsumer(unittest.TestCase):
    def test_basic(self):
        f = io.BytesIO()
        progress = []
        fc = transit.FileConsumer(f, progress.append)
        self.assertEqual(progress, [])
        self.assertEqual(f.getvalue(), b"")
        fc.write(b"." * 99)
        self.assertEqual(progress, [99])
        self.assertEqual(f.getvalue(), b"." * 99)
        fc.write(b"!")
        self.assertEqual(progress, [99, 1])
        self.assertEqual(f.getvalue(), b"." * 99 + b"!")

    def test_hasher(self):
        hashee = []
        f = io.BytesIO()
        progress = []
        fc = transit.FileConsumer(f, progress.append, hasher=hashee.append)
        self.assertEqual(progress, [])
        self.assertEqual(f.getvalue(), b"")
        self.assertEqual(hashee, [])
        fc.write(b"." * 99)
        self.assertEqual(progress, [99])
        self.assertEqual(f.getvalue(), b"." * 99)
        self.assertEqual(hashee, [b"." * 99])
        fc.write(b"!")
        self.assertEqual(progress, [99, 1])
        self.assertEqual(f.getvalue(), b"." * 99 + b"!")
        self.assertEqual(hashee, [b"." * 99, b"!"])


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


class Transit(unittest.TestCase):
    def setUp(self):
        self._connectors = []
        self._waiters = []
        self._descriptions = []

    def _start_connector(self, ep, description, is_relay=False):
        d = defer.Deferred()
        self._connectors.append(ep)
        self._waiters.append(d)
        self._descriptions.append(description)
        return d

    @inlineCallbacks
    def test_success_direct(self):
        reactor = mock.Mock()
        s = transit.TransitSender("", reactor=reactor)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()  # start the listener
        del hints
        s.add_connection_hints([
            DIRECT_HINT_JSON, UNRECOGNIZED_DIRECT_HINT_JSON,
            UNRECOGNIZED_HINT_JSON
        ])

        s._start_connector = self._start_connector
        d = s.connect()
        self.assertNoResult(d)
        self.assertEqual(len(self._waiters), 1)
        self.assertIsInstance(self._waiters[0], defer.Deferred)

        self._waiters[0].callback("winner")
        self.assertEqual(self.successResultOf(d), "winner")
        self.assertEqual(self._descriptions, ["->tcp:direct:1234"])

    @inlineCallbacks
    def test_success_direct_tor(self):
        clock = task.Clock()
        s = transit.TransitSender("", tor=mock.Mock(), reactor=clock)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()  # start the listener
        del hints
        s.add_connection_hints([DIRECT_HINT_JSON])

        s._start_connector = self._start_connector
        d = s.connect()
        self.assertNoResult(d)
        self.assertEqual(len(self._waiters), 1)
        self.assertIsInstance(self._waiters[0], defer.Deferred)

        self._waiters[0].callback("winner")
        self.assertEqual(self.successResultOf(d), "winner")
        self.assertEqual(self._descriptions, ["tor->tcp:direct:1234"])

    @inlineCallbacks
    def test_success_direct_tor_relay(self):
        clock = task.Clock()
        s = transit.TransitSender("", tor=mock.Mock(), reactor=clock)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()  # start the listener
        del hints
        s.add_connection_hints([RELAY_HINT_JSON])

        s._start_connector = self._start_connector
        d = s.connect()
        # move the clock forward any amount, since relay connections are
        # triggered starting at T+0.0
        clock.advance(1.0)
        self.assertNoResult(d)
        self.assertEqual(len(self._waiters), 1)
        self.assertIsInstance(self._waiters[0], defer.Deferred)

        self._waiters[0].callback("winner")
        self.assertEqual(self.successResultOf(d), "winner")
        self.assertEqual(self._descriptions, ["tor->relay:tcp:relay:1234"])

    def _endpoint_from_hint_obj(self, hint, _tor, _reactor):
        if isinstance(hint, DirectTCPV1Hint):
            if hint.hostname == "unavailable":
                return None
            return hint.hostname
        return None

    @inlineCallbacks
    def test_wait_for_relay(self):
        clock = task.Clock()
        s = transit.TransitSender("", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()
        del hints
        s.add_connection_hints(
            [DIRECT_HINT_JSON, UNRECOGNIZED_HINT_JSON, RELAY_HINT_JSON])
        s._start_connector = self._start_connector

        with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                        self._endpoint_from_hint_obj):
            d = s.connect()
            self.assertNoResult(d)
            # the direct connectors are tried right away, but the relay
            # connectors are stalled for a few seconds
            self.assertEqual(self._connectors, ["direct"])

            clock.advance(s.RELAY_DELAY + 1.0)
            self.assertEqual(self._connectors, ["direct", "relay"])

            self._waiters[0].callback("winner")
            self.assertEqual(self.successResultOf(d), "winner")

    @inlineCallbacks
    def test_priorities(self):
        clock = task.Clock()
        s = transit.TransitSender("", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()
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
        s._start_connector = self._start_connector

        with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                        self._endpoint_from_hint_obj):
            d = s.connect()
            self.assertNoResult(d)
            # direct connector should be used first, then the priority=3.0 relay,
            # then the two 2.0 relays, then the (default) 0.0 relay

            self.assertEqual(self._connectors, ["direct"])

            clock.advance(s.RELAY_DELAY + 1.0)
            self.assertEqual(self._connectors, ["direct", "relay3"])

            clock.advance(s.RELAY_DELAY)
            self.assertIn(self._connectors,
                          (["direct", "relay3", "relay2", "relay4"],
                           ["direct", "relay3", "relay4", "relay2"]))

            clock.advance(s.RELAY_DELAY)
            self.assertIn(self._connectors,
                          (["direct", "relay3", "relay2", "relay4", "relay"],
                           ["direct", "relay3", "relay4", "relay2", "relay"]))

            self._waiters[0].callback("winner")
            self.assertEqual(self.successResultOf(d), "winner")

    @inlineCallbacks
    def test_no_direct_hints(self):
        clock = task.Clock()
        s = transit.TransitSender("", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()  # start the listener
        del hints
        # include hints that can't be turned into an endpoint at runtime
        s.add_connection_hints([
            UNRECOGNIZED_HINT_JSON, UNAVAILABLE_HINT_JSON, RELAY_HINT2_JSON,
            UNAVAILABLE_RELAY_HINT_JSON
        ])
        s._start_connector = self._start_connector

        with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                        self._endpoint_from_hint_obj):
            d = s.connect()
            self.assertNoResult(d)
            # since there are no usable direct hints, the relay connector will
            # only be stalled for 0 seconds
            self.assertEqual(self._connectors, [])

            clock.advance(0)
            self.assertEqual(self._connectors, ["relay"])

            self._waiters[0].callback("winner")
            self.assertEqual(self.successResultOf(d), "winner")

    @inlineCallbacks
    def test_no_contenders(self):
        clock = task.Clock()
        s = transit.TransitSender("", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints()  # start the listener
        del hints
        s.add_connection_hints([])  # no hints at all
        s._start_connector = self._start_connector

        with mock.patch("wormhole.transit.endpoint_from_hint_obj",
                        self._endpoint_from_hint_obj):
            d = s.connect()
            f = self.failureResultOf(d, transit.TransitError)
            self.assertEqual(str(f.value), "No contenders for connection")


class RelayHandshake(unittest.TestCase):
    def old_build_relay_handshake(self, key):
        token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
        return (token, b"please relay " + hexlify(token) + b"\n")

    def test_old(self):
        key = b"\x00"
        token, old_handshake = self.old_build_relay_handshake(key)
        tc = transit_server.TransitConnection()
        tc.factory = mock.Mock()
        tc.factory.connection_got_token = mock.Mock()
        tc.dataReceived(old_handshake[:-1])
        self.assertEqual(tc.factory.connection_got_token.mock_calls, [])
        tc.dataReceived(old_handshake[-1:])
        self.assertEqual(tc.factory.connection_got_token.mock_calls,
                         [mock.call(hexlify(token), None, tc)])

    def test_new(self):
        c = transit.Common(None)
        c.set_transit_key(b"\x00")
        new_handshake = c._build_relay_handshake()
        token, old_handshake = self.old_build_relay_handshake(b"\x00")

        tc = transit_server.TransitConnection()
        tc.factory = mock.Mock()
        tc.factory.connection_got_token = mock.Mock()
        tc.dataReceived(new_handshake[:-1])
        self.assertEqual(tc.factory.connection_got_token.mock_calls, [])
        tc.dataReceived(new_handshake[-1:])
        self.assertEqual(
            tc.factory.connection_got_token.mock_calls,
            [mock.call(hexlify(token), c._side.encode("ascii"), tc)])


class Full(ServerBase, unittest.TestCase):
    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_direct(self):
        KEY = b"k" * 32
        s = transit.TransitSender(None)
        r = transit.TransitReceiver(None)

        s.set_transit_key(KEY)
        r.set_transit_key(KEY)

        # TODO: this sometimes fails with EADDRINUSE
        shints = yield s.get_connection_hints()
        rhints = yield r.get_connection_hints()

        s.add_connection_hints(rhints)
        r.add_connection_hints(shints)

        (x, y) = yield self.doBoth(s.connect(), r.connect())
        self.assertIsInstance(x, transit.Connection)
        self.assertIsInstance(y, transit.Connection)

        d = y.receive_record()

        x.send_record(b"record1")
        r = yield d
        self.assertEqual(r, b"record1")

        yield x.close()
        yield y.close()

    @inlineCallbacks
    def test_relay(self):
        KEY = b"k" * 32
        s = transit.TransitSender(self.transit, no_listen=True)
        r = transit.TransitReceiver(self.transit, no_listen=True)

        s.set_transit_key(KEY)
        r.set_transit_key(KEY)

        shints = yield s.get_connection_hints()
        rhints = yield r.get_connection_hints()

        s.add_connection_hints(rhints)
        r.add_connection_hints(shints)

        (x, y) = yield self.doBoth(s.connect(), r.connect())
        self.assertIsInstance(x, transit.Connection)
        self.assertIsInstance(y, transit.Connection)

        d = y.receive_record()

        x.send_record(b"record1")
        r = yield d
        self.assertEqual(r, b"record1")

        yield x.close()
        yield y.close()
