from __future__ import print_function, absolute_import, unicode_literals
import wormhole
from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks, gatherResults
from twisted.internet.protocol import Protocol, Factory
from twisted.trial import unittest

from ..common import ServerBase
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
    def test_full(self):
        raise unittest.SkipTest("not ready yet")
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _enable_dilate=True)
        w1.allocate_code()
        code = yield w1.get_code()
        print("code is: {}".format(code))
        w2.set_code(code)
        yield doBoth(w1.get_verifier(), w2.get_verifier())
        print("connected")

        eps1_d = w1.dilate()
        eps2_d = w2.dilate()
        (eps1, eps2) = yield doBoth(eps1_d, eps2_d)
        (control_ep1, connect_ep1, listen_ep1) = eps1
        (control_ep2, connect_ep2, listen_ep2) = eps2
        print("w.dilate ready")

        f1 = Factory()
        f1.protocol = L
        f1.d = Deferred()
        f1.d.addCallback(lambda data: eq.fire_eventually(data))
        d1 = control_ep1.connect(f1)

        f2 = Factory()
        f2.protocol = L
        f2.d = Deferred()
        f2.d.addCallback(lambda data: eq.fire_eventually(data))
        d2 = control_ep2.connect(f2)
        yield d1
        yield d2
        print("control endpoints connected")
        data1 = yield f1.d
        data2 = yield f2.d
        self.assertEqual(data1, b"hello\n")
        self.assertEqual(data2, b"hello\n")

        yield w1.close()
        yield w2.close()

        # TODO: this shouldn't be necessary. Also, it doesn't help.
        d = Deferred()
        reactor.callLater(1.0, d.callback, None)
        yield d
    test_full.timeout = 10
