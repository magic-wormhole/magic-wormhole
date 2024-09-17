import wormhole
from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks, gatherResults
from twisted.internet.protocol import Protocol, Factory
from twisted.trial import unittest

from ..common import ServerBase, poll_until
from ..._interfaces import IDilationConnector
from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection

APPID = u"lothar.com/dilate-test"


def doBoth(d1, d2):
    return gatherResults([d1, d2], True)


class L(Protocol):
    def connectionMade(self):
        print("got connection")
        self.transport.write(b"hello\n")

    def dataReceived(self, data):
        print("dataReceived: {}".format(data))
        self.factory.d.callback(data)

    def connectionLost(self, why):
        print("connectionLost")


class Full(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")
        # test_welcome wants to see [current_cli_version]
        yield self._setup_relay(None)

    @inlineCallbacks
    def test_control(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w1.allocate_code()
        code = yield w1.get_code()
        print("code is: {}".format(code))
        w2.set_code(code)
        yield doBoth(w1.get_verifier(), w2.get_verifier())
        print("connected")

        eps1 = w1.dilate()
        eps2 = w2.dilate()
        print("w.dilate ready")

        f1 = Factory()
        f1.protocol = L
        f1.d = Deferred()
        f1.d.addCallback(lambda data: eq.fire_eventually(data))
        d1 = eps1.control.connect(f1)

        f2 = Factory()
        f2.protocol = L
        f2.d = Deferred()
        f2.d.addCallback(lambda data: eq.fire_eventually(data))
        d2 = eps2.control.connect(f2)
        yield d1
        yield d2
        print("control endpoints connected")
        # note: I'm making some horrible assumptions about one-to-one writes
        # and reads across a TCP stack that isn't obligated to maintain such
        # a relationship, but it's much easier than doing this properly. If
        # the tests ever start failing, do the extra work, probably by
        # using a twisted.protocols.basic.LineOnlyReceiver
        data1 = yield f1.d
        data2 = yield f2.d
        self.assertEqual(data1, b"hello\n")
        self.assertEqual(data2, b"hello\n")

        yield w1.close()
        yield w2.close()
    test_control.timeout = 30


class ReconP(Protocol):
    def eventually(self, which, data):
        d = self.factory.deferreds[which]
        self.factory.eq.fire_eventually(data).addCallback(d.callback)

    def connectionMade(self):
        self.eventually("connectionMade", self)
        # self.transport.write(b"hello\n")

    def dataReceived(self, data):
        self.eventually("dataReceived", data)

    def connectionLost(self, why):
        self.eventually("connectionLost", (self, why))


class ReconF(Factory):
    protocol = ReconP

    def __init__(self, eq):
        Factory.__init__(self)
        self.eq = eq
        self.deferreds = {}
        for name in ["connectionMade", "dataReceived", "connectionLost"]:
            self.deferreds[name] = Deferred()

    def resetDeferred(self, name):
        d = Deferred()
        self.deferreds[name] = d
        return d


class Reconnect(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")
        # test_welcome wants to see [current_cli_version]
        yield self._setup_relay(None)

    @inlineCallbacks
    def test_reconnect(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        yield doBoth(w1.get_verifier(), w2.get_verifier())

        eps1 = w1.dilate()
        eps2 = w2.dilate()
        print("w.dilate ready")

        f1, f2 = ReconF(eq), ReconF(eq)
        d1, d2 = eps1.control.connect(f1), eps2.control.connect(f2)
        yield d1
        yield d2

        protocols = {}

        def p_connected(p, index):
            protocols[index] = p
            msg = "hello from %s\n" % index
            p.transport.write(msg.encode("ascii"))
        f1.deferreds["connectionMade"].addCallback(p_connected, 1)
        f2.deferreds["connectionMade"].addCallback(p_connected, 2)

        data1 = yield f1.deferreds["dataReceived"]
        data2 = yield f2.deferreds["dataReceived"]
        self.assertEqual(data1, b"hello from 2\n")
        self.assertEqual(data2, b"hello from 1\n")
        # the ACKs are now in flight and may not arrive before we kill the
        # connection

        f1.resetDeferred("connectionMade")
        f2.resetDeferred("connectionMade")
        d1 = f1.resetDeferred("dataReceived")
        d2 = f2.resetDeferred("dataReceived")

        # now we reach inside and drop the connection
        sc = protocols[1].transport
        orig_connection = sc._manager._connection
        orig_connection.disconnect()

        # stall until the connection has been replaced
        yield poll_until(lambda: sc._manager._connection
                         and (orig_connection != sc._manager._connection))

        # now write some more data, which should travel over the new
        # connection
        protocols[1].transport.write(b"more\n")
        data2 = yield d2
        self.assertEqual(data2, b"more\n")

        replacement_connection = sc._manager._connection
        self.assertNotEqual(orig_connection, replacement_connection)

        # the application-visible Protocol should not observe the
        # interruption
        self.assertNoResult(f1.deferreds["connectionMade"])
        self.assertNoResult(f2.deferreds["connectionMade"])
        self.assertNoResult(f1.deferreds["connectionLost"])
        self.assertNoResult(f2.deferreds["connectionLost"])

        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_data_while_offline(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        yield doBoth(w1.get_verifier(), w2.get_verifier())

        eps1 = w1.dilate()
        eps2 = w2.dilate()
        print("w.dilate ready")

        f1, f2 = ReconF(eq), ReconF(eq)
        d1, d2 = eps1.control.connect(f1), eps2.control.connect(f2)
        yield d1
        yield d2

        protocols = {}

        def p_connected(p, index):
            protocols[index] = p
            msg = "hello from %s\n" % index
            p.transport.write(msg.encode("ascii"))
        f1.deferreds["connectionMade"].addCallback(p_connected, 1)
        f2.deferreds["connectionMade"].addCallback(p_connected, 2)

        data1 = yield f1.deferreds["dataReceived"]
        data2 = yield f2.deferreds["dataReceived"]
        self.assertEqual(data1, b"hello from 2\n")
        self.assertEqual(data2, b"hello from 1\n")
        # the ACKs are now in flight and may not arrive before we kill the
        # connection

        f1.resetDeferred("connectionMade")
        f2.resetDeferred("connectionMade")
        d1 = f1.resetDeferred("dataReceived")
        d2 = f2.resetDeferred("dataReceived")

        # switch off connections
        assert not w1._boss._D._manager._debug_stall_connector
        cd1, cd2 = Deferred(), Deferred()
        w1._boss._D._manager._debug_stall_connector = cd1.callback
        w2._boss._D._manager._debug_stall_connector = cd2.callback

        # now we reach inside and drop the connection
        sc = protocols[1].transport
        orig_connection = sc._manager._connection
        orig_connection.disconnect()

        c1 = yield cd1
        c2 = yield cd2
        assert IDilationConnector.providedBy(c1)
        assert IDilationConnector.providedBy(c2)
        assert c1 is not orig_connection
        w1._boss._D._manager._debug_stall_connector = False
        w2._boss._D._manager._debug_stall_connector = False

        # now write some data while the connection is definitely offline
        protocols[1].transport.write(b"more 1->2\n")
        protocols[2].transport.write(b"more 2->1\n")

        # allow the connections to proceed
        c1.start()
        c2.start()

        # and wait for the data to arrive
        data2 = yield d2
        self.assertEqual(data2, b"more 1->2\n")
        data1 = yield d1
        self.assertEqual(data1, b"more 2->1\n")

        # the application-visible Protocol should not observe the
        # interruption
        self.assertNoResult(f1.deferreds["connectionMade"])
        self.assertNoResult(f2.deferreds["connectionMade"])
        self.assertNoResult(f1.deferreds["connectionLost"])
        self.assertNoResult(f2.deferreds["connectionLost"])

        yield w1.close()
        yield w2.close()


class Endpoints(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")
        # test_welcome wants to see [current_cli_version]
        yield self._setup_relay(None)

    @inlineCallbacks
    def test_endpoints(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        yield doBoth(w1.get_verifier(), w2.get_verifier())

        eps1 = w1.dilate()
        eps2 = w2.dilate()
        print("w.dilate ready")

        f0 = ReconF(eq)
        yield eps2.listen.listen(f0)

        from twisted.python import log
        f1 = ReconF(eq)
        log.msg("connecting")
        p1_client = yield eps1.connect.connect(f1)
        log.msg("sending c->s")
        p1_client.transport.write(b"hello from p1\n")
        data = yield f0.deferreds["dataReceived"]
        self.assertEqual(data, b"hello from p1\n")
        p1_server = self.successResultOf(f0.deferreds["connectionMade"])
        log.msg("sending s->c")
        p1_server.transport.write(b"hello p1\n")
        log.msg("waiting for client to receive")
        data = yield f1.deferreds["dataReceived"]
        self.assertEqual(data, b"hello p1\n")

        # open a second channel
        f0.resetDeferred("connectionMade")
        f0.resetDeferred("dataReceived")
        f1.resetDeferred("dataReceived")
        f2 = ReconF(eq)
        p2_client = yield eps1.connect.connect(f2)
        p2_server = yield f0.deferreds["connectionMade"]
        p2_server.transport.write(b"hello p2\n")
        data = yield f2.deferreds["dataReceived"]
        self.assertEqual(data, b"hello p2\n")
        p2_client.transport.write(b"hello from p2\n")
        data = yield f0.deferreds["dataReceived"]
        self.assertEqual(data, b"hello from p2\n")
        self.assertNoResult(f1.deferreds["dataReceived"])

        # now close the first subchannel (p1) from the listener side
        p1_server.transport.loseConnection()
        yield f0.deferreds["connectionLost"]
        yield f1.deferreds["connectionLost"]

        f0.resetDeferred("connectionLost")
        # and close the second from the connector side
        p2_client.transport.loseConnection()
        yield f0.deferreds["connectionLost"]
        yield f2.deferreds["connectionLost"]

        yield w1.close()
        yield w2.close()
