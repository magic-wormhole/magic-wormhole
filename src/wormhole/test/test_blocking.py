import json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults
from twisted.internet.threads import deferToThread
from ..blocking.transcribe import Wormhole as BlockingWormhole, UsageError
from .common import ServerBase

class Blocking(ServerBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def test_basic(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code)
            d1 = deferToThread(w1.get_data, b"data1")
            d2 = deferToThread(w2.get_data, b"data2")
            return gatherResults([d1,d2], True)
        d.addCallback(_got_code)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
        d.addCallback(_done)
        return d

    def test_fixed_code(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        d1 = deferToThread(w1.get_data, b"data1")
        d2 = deferToThread(w2.get_data, b"data2")
        d = gatherResults([d1,d2], True)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
        d.addCallback(_done)
        return d

    def test_errors(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.get_data, b"data")
        w1.set_code("123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, "123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w2.get_code)
        def _done(code):
            self.assertRaises(UsageError, w2.get_code)
        d.addCallback(_done)
        return d

    def test_serialize(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            self.assertRaises(UsageError, w2.serialize) # too early
            w2.set_code(code)
            w2.serialize() # ok
            s = w1.serialize()
            self.assertEqual(type(s), type(""))
            unpacked = json.loads(s) # this is supposed to be JSON
            self.assertEqual(type(unpacked), dict)
            new_w1 = BlockingWormhole.from_serialized(s)
            d1 = deferToThread(new_w1.get_data, b"data1")
            d2 = deferToThread(w2.get_data, b"data2")
            return gatherResults([d1,d2], True)
        d.addCallback(_got_code)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            self.assertRaises(UsageError, w2.serialize) # too late
        d.addCallback(_done)
        return d
    test_serialize.skip = "not yet implemented for the blocking flavor"
