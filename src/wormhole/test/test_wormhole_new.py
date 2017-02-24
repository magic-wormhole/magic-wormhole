from __future__ import print_function, unicode_literals
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from .common import ServerBase
from .. import wormhole

APPID = "appid"

class Delegate:
    def __init__(self):
        self.code = None
        self.verifier = None
        self.messages = []
        self.closed = None
    def wormhole_got_code(self, code):
        self.code = code
    def wormhole_got_verifier(self, verifier):
        self.verifier = verifier
    def wormhole_receive(self, data):
        self.messages.append(data)
    def wormhole_closed(self, result):
        self.closed = result

class New(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_allocate(self):
        w = wormhole.deferred_wormhole(APPID, self.relayurl, reactor)
        w.allocate_code(2)
        code = yield w.when_code()
        print("code:", code)
        yield w.close()
    test_allocate.timeout = 2

    def test_delegated(self):
        dg = Delegate()
        w = wormhole.delegated_wormhole(APPID, self.relayurl, reactor, dg)
        w.close()

    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.deferred_wormhole(APPID, self.relayurl, reactor)
        w1.allocate_code(2)
        code = yield w1.when_code()
        print("code:", code)
        w2 = wormhole.deferred_wormhole(APPID, self.relayurl, reactor)
        w2.set_code(code)
        code2 = yield w2.when_code()
        self.assertEqual(code, code2)

        w1.send(b"data")

        data = yield w2.when_received()
        self.assertEqual(data, b"data")

        yield w1.close()
        yield w2.close()
    test_basic.timeout = 2
