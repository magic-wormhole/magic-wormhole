from __future__ import print_function
import io
from binascii import hexlify, unhexlify
from twisted.trial import unittest
from twisted.internet import defer, task, endpoints, protocol, address, error
from twisted.internet.defer import gatherResults, inlineCallbacks
from twisted.python import log, failure
from twisted.test import proto_helpers
from .. import transit
from ..errors import UsageError
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError

class Highlander(unittest.TestCase):
    def test_one_winner(self):
        cancelled = set()
        contenders = [defer.Deferred(lambda d: cancelled.add(i))
                      for i in range(4)]
        result = []
        d = transit.there_can_be_only_one(contenders)
        d.addBoth(result.append)
        self.assertEqual(result, [])
        contenders[0].errback(ValueError())
        self.assertEqual(result, [])
        contenders[1].errback(TypeError())
        self.assertEqual(result, [])
        contenders[2].callback("yay")
        self.assertEqual(result, ["yay"])
        self.assertEqual(cancelled, set([3]))

    def test_there_might_also_be_none(self):
        cancelled = set()
        contenders = [defer.Deferred(lambda d: cancelled.add(i))
                      for i in range(4)]
        result = []
        d = transit.there_can_be_only_one(contenders)
        d.addBoth(result.append)
        self.assertEqual(result, [])
        contenders[0].errback(ValueError())
        self.assertEqual(result, [])
        contenders[1].errback(TypeError())
        self.assertEqual(result, [])
        contenders[2].errback(TypeError())
        self.assertEqual(result, [])
        contenders[3].errback(NameError())
        self.assertEqual(len(result), 1)
        f = result[0]
        self.assertIsInstance(f.value, ValueError) # first failure is recorded
        self.assertEqual(cancelled, set())

    def test_cancel_early(self):
        cancelled = set()
        contenders = [defer.Deferred(lambda d, i=i: cancelled.add(i))
                      for i in range(4)]
        result = []
        d = transit.there_can_be_only_one(contenders)
        d.addBoth(result.append)
        self.assertEqual(result, [])
        self.assertEqual(cancelled, set())
        d.cancel()
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].value, defer.CancelledError)
        self.assertEqual(cancelled, set(range(4)))

    def test_cancel_after_one_failure(self):
        cancelled = set()
        contenders = [defer.Deferred(lambda d, i=i: cancelled.add(i))
                      for i in range(4)]
        result = []
        d = transit.there_can_be_only_one(contenders)
        d.addBoth(result.append)
        self.assertEqual(result, [])
        self.assertEqual(cancelled, set())
        contenders[0].errback(ValueError())
        d.cancel()
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].value, ValueError)
        self.assertEqual(cancelled, set([1,2,3]))

class Forever(unittest.TestCase):
    def _forever_setup(self):
        clock = task.Clock()
        c = transit.Common(u"", reactor=clock)
        cancelled = []
        result = []
        d0 = defer.Deferred(cancelled.append)
        d = c._not_forever(1.0, d0)
        d.addBoth(result.append)
        return c, clock, d0, d, cancelled, result

    def test_not_forever_fires(self):
        c, clock, d0, d, cancelled, result = self._forever_setup()
        self.assertEqual((result, cancelled), ([], []))
        d.callback(1)
        self.assertEqual((result, cancelled), ([1], []))
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_errs(self):
        c, clock, d0, d, cancelled, result = self._forever_setup()
        self.assertEqual((result, cancelled), ([], []))
        d.errback(ValueError())
        self.assertEqual(cancelled, [])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].value, ValueError)
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_cancel_early(self):
        c, clock, d0, d, cancelled, result = self._forever_setup()
        self.assertEqual((result, cancelled), ([], []))
        d.cancel()
        self.assertEqual(cancelled, [d0])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].value, defer.CancelledError)
        self.assertNot(clock.getDelayedCalls())

    def test_not_forever_timeout(self):
        c, clock, d0, d, cancelled, result = self._forever_setup()
        self.assertEqual((result, cancelled), ([], []))
        clock.advance(2.0)
        self.assertEqual(cancelled, [d0])
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0].value, defer.CancelledError)
        self.assertNot(clock.getDelayedCalls())

class Misc(unittest.TestCase):
    def test_allocate_port(self):
        portno = transit.allocate_tcp_port()
        self.assertIsInstance(portno, int)

class Hints(unittest.TestCase):
    def test_endpoint_from_hint_obj(self):
        c = transit.Common(u"")
        ep = c._endpoint_from_hint_obj(transit.DirectTCPV1Hint("localhost", 1234))
        self.assertIsInstance(ep, endpoints.HostnameEndpoint)
        ep = c._endpoint_from_hint_obj("unknown:stuff:yowza:pivlor")
        self.assertEqual(ep, None)


class Basic(unittest.TestCase):
    @inlineCallbacks
    def test_relay_hints(self):
        URL = u"tcp:host:1234"
        c = transit.Common(URL, no_listen=True)
        hints = yield c.get_connection_hints()
        self.assertEqual(hints, [{"type": "relay-v1",
                                  "hints": [{"type": "direct-tcp-v1",
                                            "hostname": u"host",
                                            "port": 1234}],
                                  }])
        self.assertRaises(UsageError, transit.Common, 123)

    @inlineCallbacks
    def test_no_relay_hints(self):
        c = transit.Common(None, no_listen=True)
        hints = yield c.get_connection_hints()
        self.assertEqual(hints, [])

    def test_ignore_bad_hints(self):
        c = transit.Common(u"")
        c.add_connection_hints([{"type": "unknown"}])
        c.add_connection_hints([{"type": "relay-v1",
                                 "hints": [{"type": "unknown"}]}])
        self.assertEqual(c._their_direct_hints, [])
        self.assertEqual(c._their_relay_hints, [])

    def test_ignore_localhost_hint(self):
        # this actually starts the listener
        c = transit.TransitSender(u"")
        results = []
        d = c.get_connection_hints()
        d.addBoth(results.append)
        hints = results[0]
        c._stop_listening()
        for hint in hints:
            self.assertFalse(hint[u'hostname'] == u'127.0.0.1')

    def test_transit_key_wait(self):
        KEY = b"123"
        c = transit.Common(u"")
        results = []
        d = c._get_transit_key()
        d.addBoth(results.append)
        self.assertEqual(results, [])
        c.set_transit_key(KEY)
        self.assertEqual(results, [KEY])

    def test_transit_key_already_set(self):
        KEY = b"123"
        c = transit.Common(u"")
        c.set_transit_key(KEY)
        results = []
        d = c._get_transit_key()
        d.addBoth(results.append)
        self.assertEqual(results, [KEY])

    def test_transit_keys(self):
        KEY = b"123"
        s = transit.TransitSender(u"")
        s.set_transit_key(KEY)
        r = transit.TransitReceiver(u"")
        r.set_transit_key(KEY)

        self.assertEqual(s._send_this(), b"transit sender 559bdeae4b49fa6a23378d2b68f4c7e69378615d4af049c371c6a26e82391089 ready\n\n")
        self.assertEqual(s._send_this(), r._expect_this())

        self.assertEqual(r._send_this(), b"transit receiver ed447528194bac4c00d0c854b12a97ce51413d89aa74d6304475f516fdc23a1b ready\n\n")
        self.assertEqual(r._send_this(), s._expect_this())

        self.assertEqual(hexlify(s._sender_record_key()), b"5a2fba3a9e524ab2e2823ff53b05f946896f6e4ce4e282ffd8e3ac0e5e9e0cda")
        self.assertEqual(hexlify(s._sender_record_key()),
                         hexlify(r._receiver_record_key()))

        self.assertEqual(hexlify(r._sender_record_key()), b"eedb143117249f45b39da324decf6bd9aae33b7ccd58487436de611a3c6b871d")
        self.assertEqual(hexlify(r._sender_record_key()),
                         hexlify(s._receiver_record_key()))

    def test_connection_ready(self):
        s = transit.TransitSender(u"")
        self.assertEqual(s.connection_ready("p1"), "go")
        self.assertEqual(s._winner, "p1")
        self.assertEqual(s.connection_ready("p2"), "nevermind")
        self.assertEqual(s._winner, "p1")

        r = transit.TransitReceiver(u"")
        self.assertEqual(r.connection_ready("p1"), "wait-for-decision")
        self.assertEqual(r.connection_ready("p2"), "wait-for-decision")


class Listener(unittest.TestCase):
    def test_listener(self):
        c = transit.Common(u"")
        hints, ep = c._build_listener()
        self.assertIsInstance(hints, (list, set))
        if hints:
            self.assertIsInstance(hints[0], transit.DirectTCPV1Hint)
        self.assertIsInstance(ep, endpoints.TCP4ServerEndpoint)

    def test_get_direct_hints(self):
        # this actually starts the listener
        c = transit.TransitSender(u"")

        results = []
        d = c.get_connection_hints()
        d.addBoth(results.append)
        self.assertEqual(len(results), 1)
        hints = results[0]

        # the hints are supposed to be cached, so calling this twice won't
        # start a second listener
        self.assert_(c._listener)
        results = []
        d = c.get_connection_hints()
        d.addBoth(results.append)
        self.assertEqual(results, [hints])

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
        #print("oDR", self._count, len(self.buf))
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
        self.assertEqual(f._describePeer(addrU),
                         "<-UNIXAddress('/dev/unlikely')")

    def test_success(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        results = []
        d = f.whenDone()
        d.addBoth(results.append)
        self.assertEqual(results, [])

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
        self.assertEqual(results, [])
        self.assertEqual(p._description, "<-example.com:1234")

        p._d.callback(p)
        self.assertEqual(results, [p])

    def test_one_fail_one_success(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        results = []
        d = f.whenDone()
        d.addBoth(results.append)
        self.assertEqual(results, [])

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertEqual(results, [])

        p1._d.errback(transit.BadHandshake("nope"))
        self.assertEqual(results, [])
        p2._d.callback(p2)
        self.assertEqual(results, [p2])

    def test_first_success_wins(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        results = []
        d = f.whenDone()
        d.addBoth(results.append)
        self.assertEqual(results, [])

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertEqual(results, [])

        p1._d.callback(p1)
        self.assertEqual(results, [p1])
        self.assertEqual(p1._cancelled, False)
        self.assertEqual(p2._cancelled, True)

    def test_log_other_errors(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        results = []
        d = f.whenDone()
        d.addBoth(results.append)
        self.assertEqual(results, [])

        addr = address.HostnameAddress("example.com", 1234)
        p1 = f.buildProtocol(addr)

        # if the Connection protocol throws an unexpected error, that should
        # get logged to the Twisted logs (as an Unhandled Error in Deferred)
        # so we can diagnose the bug
        f.connectionWasMade(p1)
        p1._d.errback(RandomError("boom"))
        self.assertEqual(len(results), 0)

        log.msg("=== note: the next RandomError is expected ===")
        # Make sure the Deferred has gone out of scope, so the UnhandledError
        # happens quickly. We must manually break the gc cycle.
        del p1._d
        self.flushLoggedErrors(RandomError)
        log.msg("=== note: the preceding RandomError was expected ===")

    def test_cancel(self):
        f = transit.InboundConnectionFactory("owner")
        f.protocol = MockConnection
        results = []
        d = f.whenDone()
        d.addBoth(results.append)
        self.assertEqual(results, [])

        addr1 = address.HostnameAddress("example.com", 1234)
        addr2 = address.HostnameAddress("example.com", 5678)
        p1 = f.buildProtocol(addr1)
        p2 = f.buildProtocol(addr2)

        f.connectionWasMade(p1)
        f.connectionWasMade(p2)
        self.assertEqual(results, [])

        d.cancel()

        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, defer.CancelledError)
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
        f.connectionWasMade(p) # no-op for outbound
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
        return b"s"*32
    def _receiver_record_key(self):
        return b"r"*32

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
        self.assertEqual(str(e),
                         "got %r want %r" % (b'unexpected', b'expectation'))
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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(t.read_buf(), b"go\n")
        self.assertEqual(t._connected, True)
        self.assertEqual(c.state, "records")
        self.assertEqual(results, [c])

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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(t.read_buf(), b"nevermind\n")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, transit.BadHandshake)
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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])
        c.state = RandomError("boom")
        self.assertRaises(RandomError, c.dataReceived, b"surprise!")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, RandomError)

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
        self.assertEqual(t.read_buf(), b"") # quiet until startNegotiation

        owner._state = "go"
        d = c.startNegotiation()
        self.assertEqual(t.read_buf(), relay_handshake)
        self.assertEqual(c.state, "relay") # waiting for OK from relay

        c.dataReceived(b"ok\n")
        self.assertEqual(t.read_buf(), b"send_this")
        self.assertEqual(c.state, "handshake")

        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "records")
        self.assertEqual(results, [c])

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
        self.assertEqual(t.read_buf(), b"") # quiet until startNegotiation

        owner._state = "go"
        d = c.startNegotiation()
        self.assertEqual(t.read_buf(), relay_handshake)
        self.assertEqual(c.state, "relay") # waiting for OK from relay

        c.dataReceived(b"not ok\n")
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")

        results = []
        d.addBoth(results.append)
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, transit.BadHandshake)
        self.assertEqual(str(f.value),
                         "got %r want %r" % (b"not ok\n", b"ok\n"))

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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertEqual(results, [])

        c.dataReceived(b"go\n")
        self.assertEqual(c.state, "records")
        self.assertEqual(results, [c])

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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertEqual(results, [])

        c.dataReceived(b"nevermind\n") # polite rejection
        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, transit.BadHandshake)
        self.assertEqual(str(f.value),
                         "got %r want %r" % (b"nevermind\n", b"go\n"))

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
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])

        c.dataReceived(b"expect_this")
        self.assertEqual(c.state, "wait-for-decision")
        self.assertEqual(results, [])

        t.loseConnection()
        self.assertEqual(t._connected, False)
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, transit.BadHandshake)
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
        results = []
        d.addBoth(results.append)
        # while we're waiting for negotiation, we get cancelled
        d.cancel()

        self.assertEqual(t._connected, False)
        self.assertEqual(c.state, "hung up")
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, defer.CancelledError)

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
        results = []
        d.addBoth(results.append)
        # while we're waiting for negotiation, the timer expires
        clock.advance(transit.TIMEOUT + 1.0)

        self.assertEqual(t._connected, False)
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, transit.BadHandshake)
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
        results = []
        d.addBoth(results.append)
        c.dataReceived(b"expect_this")
        self.assertEqual(results, [c])
        t.read_buf() # flush input buffer, prepare for encrypted records

        return t, c, owner

    def test_records_good(self):
        # now make sure that outbound records are encrypted properly
        t, c, owner = self.make_connection()

        RECORD1 = b"record"
        c.send_record(RECORD1)
        buf = t.read_buf()
        expected = ("%08x" % (24+len(RECORD1)+16)).encode("ascii")
        self.assertEqual(hexlify(buf[:4]), expected)
        encrypted = buf[4:]
        receive_box = SecretBox(owner._sender_record_key())
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE] # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        self.assertEqual(nonce, 0) # first message gets nonce 0
        decrypted = receive_box.decrypt(encrypted)
        self.assertEqual(decrypted, RECORD1)

        # second message gets nonce 1
        RECORD2 = b"record2"
        c.send_record(RECORD2)
        buf = t.read_buf()
        expected = ("%08x" % (24+len(RECORD2)+16)).encode("ascii")
        self.assertEqual(hexlify(buf[:4]), expected)
        encrypted = buf[4:]
        receive_box = SecretBox(owner._sender_record_key())
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE] # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        self.assertEqual(nonce, 1)
        decrypted = receive_box.decrypt(encrypted)
        self.assertEqual(decrypted, RECORD2)

        # and that we can receive records properly
        inbound_records = []
        c.recordReceived = inbound_records.append
        send_box = SecretBox(owner._receiver_record_key())

        RECORD3 = b"record3"
        nonce_buf = unhexlify("%048x" % 0) # first nonce must be 0
        encrypted = send_box.encrypt(RECORD3, nonce_buf)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        c.dataReceived(length[:2])
        c.dataReceived(length[2:])
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [])
        c.dataReceived(encrypted[-2:])
        self.assertEqual(inbound_records, [RECORD3])

        RECORD4 = b"record4"
        nonce_buf = unhexlify("%048x" % 1) # nonces increment
        encrypted = send_box.encrypt(RECORD4, nonce_buf)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        c.dataReceived(length[:2])
        c.dataReceived(length[2:])
        c.dataReceived(encrypted[:-2])
        self.assertEqual(inbound_records, [RECORD3])
        c.dataReceived(encrypted[-2:])
        self.assertEqual(inbound_records, [RECORD3, RECORD4])

        # receiving two records at the same time: deliver both
        inbound_records[:] = []
        RECORD5 = b"record5"
        nonce_buf = unhexlify("%048x" % 2) # nonces increment
        encrypted = send_box.encrypt(RECORD5, nonce_buf)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        r5 = length+encrypted
        RECORD6 = b"record6"
        nonce_buf = unhexlify("%048x" % 3) # nonces increment
        encrypted = send_box.encrypt(RECORD6, nonce_buf)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        r6 = length+encrypted
        c.dataReceived(r5+r6)
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
        nonce_buf = unhexlify("%048x" % 0) # first nonce must be 0
        encrypted = self.corrupt(send_box.encrypt(RECORD, nonce_buf))
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
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
        nonce_buf = unhexlify("%048x" % 1) # first nonce must be 0
        encrypted = send_box.encrypt(RECORD, nonce_buf)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
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
        results = [[] for i in range(5)]
        c.recordReceived(b"0")
        c.recordReceived(b"1")
        c.recordReceived(b"2")
        c.receive_record().addBoth(results[0].append)
        self.assertEqual(results[0], [b"0"])
        d1 = c.receive_record()
        d2 = c.receive_record()
        # they must fire in order of receipt, not order of addCallback
        d2.addBoth(results[2].append)
        self.assertEqual(results[2], [b"2"])
        d1.addBoth(results[1].append)
        self.assertEqual(results[1], [b"1"])

        c.receive_record().addBoth(results[3].append)
        c.receive_record().addBoth(results[4].append)
        self.assertEqual(results[3], [])
        self.assertEqual(results[4], [])

        c.recordReceived(b"3")
        self.assertEqual(results[3], [b"3"])
        self.assertEqual(results[4], [])

        c.recordReceived(b"4")
        self.assertEqual(results[3], [b"3"])
        self.assertEqual(results[4], [b"4"])

        closed = []
        c.receive_record().addBoth(closed.append)
        c.close()
        self.assertEqual(len(closed), 1)
        f = closed[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, error.ConnectionClosed)

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
        c._negotiation_d.addErrback(lambda err: None) # eat it
        c.transport = proto_helpers.StringTransport()
        c.recordReceived(b"r1.")

        consumer = proto_helpers.StringTransport()
        results = []
        d = c.connectConsumer(consumer, expected=10)
        d.addBoth(results.append)
        self.assertEqual(consumer.value(), b"r1.")
        self.assertEqual(results, [])

        c.recordReceived(b"r2.")
        self.assertEqual(consumer.value(), b"r1.r2.")
        self.assertEqual(results, [])

        c.recordReceived(b"r3.")
        self.assertEqual(consumer.value(), b"r1.r2.r3.")
        self.assertEqual(results, [])

        c.recordReceived(b"!")
        self.assertEqual(consumer.value(), b"r1.r2.r3.!")
        self.assertEqual(results, [10])

        # that should automatically disconnect the consumer, and subsequent
        # records should get queued, not delivered
        self.assertIs(c._consumer, None)
        c.recordReceived(b"overflow")
        self.assertEqual(consumer.value(), b"r1.r2.r3.!")

        # now test that the Deferred errbacks when the connection is lost
        results = []
        d = c.connectConsumer(consumer, expected=10)
        d.addBoth(results.append)

        c.connectionLost()
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, error.ConnectionClosed)

    def test_writeToFile(self):
        c = transit.Connection(None, None, None, "description")
        c._negotiation_d.addErrback(lambda err: None) # eat it
        c.transport = proto_helpers.StringTransport()
        c.recordReceived(b"r1.")

        f = io.BytesIO()
        progress = []
        results = []
        d = c.writeToFile(f, 10, progress.append)
        d.addBoth(results.append)
        self.assertEqual(f.getvalue(), b"r1.")
        self.assertEqual(progress, [3])
        self.assertEqual(results, [])

        c.recordReceived(b"r2.")
        self.assertEqual(f.getvalue(), b"r1.r2.")
        self.assertEqual(progress, [3, 3])
        self.assertEqual(results, [])

        c.recordReceived(b"r3.")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.")
        self.assertEqual(progress, [3, 3, 3])
        self.assertEqual(results, [])

        c.recordReceived(b"!")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.!")
        self.assertEqual(progress, [3, 3, 3, 1])
        self.assertEqual(results, [10])

        # that should automatically disconnect the consumer, and subsequent
        # records should get queued, not delivered
        self.assertIs(c._consumer, None)
        c.recordReceived(b"overflow.")
        self.assertEqual(f.getvalue(), b"r1.r2.r3.!")
        self.assertEqual(progress, [3, 3, 3, 1])

        # test what happens when enough data is queued ahead of time
        c.recordReceived(b"second.") # now "overflow.second."
        c.recordReceived(b"third.") # now "overflow.second.third."
        f = io.BytesIO()
        results = []
        d = c.writeToFile(f, 10)
        d.addBoth(results.append)
        self.assertEqual(f.getvalue(), b"overflow.second.") # whole records
        self.assertEqual(results, [16])
        self.assertEqual(list(c._inbound_records), [b"third."])

        # now test that the Deferred errbacks when the connection is lost
        results = []
        d = c.writeToFile(f, 10)
        d.addBoth(results.append)

        c.connectionLost()
        self.assertEqual(len(results), 1)
        f = results[0]
        self.assertIsInstance(f, failure.Failure)
        self.assertIsInstance(f.value, error.ConnectionClosed)

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
        fc.write(b"."* 99)
        self.assertEqual(progress, [99])
        self.assertEqual(f.getvalue(), b"."*99)
        fc.write(b"!")
        self.assertEqual(progress, [99, 1])
        self.assertEqual(f.getvalue(), b"."*99+b"!")


DIRECT_HINT = {u"type": u"direct-tcp-v1",
               u"hostname": u"direct", u"port": 1234}
RELAY_HINT = {u"type": u"relay-v1",
              u"hints": [{u"type": u"direct-tcp-v1",
                          u"hostname": u"relay", u"port": 1234}]}
UNUSABLE_HINT = {u"type": u"unknown"}
RELAY_HINT2 = {u"type": u"relay-v1",
               u"hints": [{u"type": u"direct-tcp-v1",
                           u"hostname": u"relay", u"port": 1234},
                          UNUSABLE_HINT]}
DIRECT_HINT_INTERNAL = transit.DirectTCPV1Hint(u"direct", 1234)
RELAY_HINT_FIRST = transit.DirectTCPV1Hint(u"relay", 1234)
RELAY_HINT_INTERNAL = transit.RelayV1Hint([RELAY_HINT_FIRST])

class Transit(unittest.TestCase):
    @inlineCallbacks
    def test_success_direct(self):
        clock = task.Clock()
        s = transit.TransitSender(u"", reactor=clock)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints() # start the listener
        del hints
        s.add_connection_hints([DIRECT_HINT, UNUSABLE_HINT])

        connectors = []
        def _start_connector(ep, description, is_relay=False):
            d = defer.Deferred()
            connectors.append(d)
            return d
        s._start_connector = _start_connector
        d = s.connect()
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])
        self.assertEqual(len(connectors), 1)
        self.assertIsInstance(connectors[0], defer.Deferred)

        connectors[0].callback("winner")
        self.assertEqual(results, ["winner"])

    def _endpoint_from_hint_obj(self, hint):
        if hint == DIRECT_HINT_INTERNAL:
            return "direct"
        elif hint == RELAY_HINT_FIRST:
            return "relay"
        else:
            return None

    @inlineCallbacks
    def test_wait_for_relay(self):
        clock = task.Clock()
        s = transit.TransitSender(u"", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints() # start the listener
        del hints
        s.add_connection_hints([DIRECT_HINT, UNUSABLE_HINT, RELAY_HINT])

        direct_connectors = []
        relay_connectors = []
        s._endpoint_from_hint_obj = self._endpoint_from_hint_obj
        def _start_connector(ep, description, is_relay=False):
            d = defer.Deferred()
            if ep == "direct":
                direct_connectors.append(d)
            elif ep == "relay":
                relay_connectors.append(d)
            else:
                raise ValueError
            return d
        s._start_connector = _start_connector

        d = s.connect()
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])
        # the direct connectors are tried right away, but the relay
        # connectors are stalled for a few seconds
        self.assertEqual(len(direct_connectors), 1)
        self.assertEqual(len(relay_connectors), 0)

        clock.advance(s.RELAY_DELAY + 1.0)
        self.assertEqual(len(direct_connectors), 1)
        self.assertEqual(len(relay_connectors), 1)

        direct_connectors[0].callback("winner")
        self.assertEqual(results, ["winner"])

    @inlineCallbacks
    def test_no_direct_hints(self):
        clock = task.Clock()
        s = transit.TransitSender(u"", reactor=clock, no_listen=True)
        s.set_transit_key(b"key")
        hints = yield s.get_connection_hints() # start the listener
        del hints
        s.add_connection_hints([UNUSABLE_HINT, RELAY_HINT2])

        direct_connectors = []
        relay_connectors = []
        s._endpoint_from_hint_obj = self._endpoint_from_hint_obj
        def _start_connector(ep, description, is_relay=False):
            d = defer.Deferred()
            if ep == "direct":
                direct_connectors.append(d)
            elif ep == "relay":
                relay_connectors.append(d)
            else:
                raise ValueError
            return d
        s._start_connector = _start_connector

        d = s.connect()
        results = []
        d.addBoth(results.append)
        self.assertEqual(results, [])
        # since there are no usable direct hints, the relay connector will
        # only be stalled for 0 seconds
        self.assertEqual(len(direct_connectors), 0)
        self.assertEqual(len(relay_connectors), 0)

        clock.advance(0)
        self.assertEqual(len(direct_connectors), 0)
        self.assertEqual(len(relay_connectors), 1)

        relay_connectors[0].callback("winner")
        self.assertEqual(results, ["winner"])


class Full(unittest.TestCase):
    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_full(self):
        KEY = b"k"*32
        s = transit.TransitSender(None)
        r = transit.TransitReceiver(None)

        s.set_transit_key(KEY)
        r.set_transit_key(KEY)

        shints = yield s.get_connection_hints()
        rhints = yield r.get_connection_hints()

        s.add_connection_hints(rhints)
        r.add_connection_hints(shints)

        (x,y) = yield self.doBoth(s.connect(), r.connect())
        self.assertIsInstance(x, transit.Connection)
        self.assertIsInstance(y, transit.Connection)

        d = y.receive_record()

        x.send_record(b"record1")
        r = yield d
        self.assertEqual(r, b"record1")

        yield x.close()
        yield y.close()
