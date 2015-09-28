import sys, json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults
from ..twisted.transcribe import Wormhole, UsageError
from .common import ServerBase

class Basic(ServerBase, unittest.TestCase):
    def test_basic(self):
        appid = b"appid"
        w1 = Wormhole(appid, self.relayurl)
        w2 = Wormhole(appid, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            d1 = w1.get_data(b"data1")
            d2 = w2.get_data(b"data2")
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
        w1 = Wormhole(appid, self.relayurl)
        w2 = Wormhole(appid, self.relayurl)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        d1 = w1.get_data(b"data1")
        d2 = w2.get_data(b"data2")
        d = gatherResults([d1,d2], True)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
        d.addCallback(_done)
        return d

    def test_errors(self):
        appid = b"appid"
        w1 = Wormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.get_data, b"data")
        w1.set_code("123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, "123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = Wormhole(appid, self.relayurl)
        d = w2.get_code()
        self.assertRaises(UsageError, w2.get_code)
        return d

    def test_serialize(self):
        appid = b"appid"
        w1 = Wormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = Wormhole(appid, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            self.assertRaises(UsageError, w2.serialize) # too early
            w2.set_code(code)
            w2.serialize() # ok
            s = w1.serialize()
            self.assertEqual(type(s), type(""))
            unpacked = json.loads(s) # this is supposed to be JSON
            self.assertEqual(type(unpacked), dict)
            new_w1 = Wormhole.from_serialized(s)
            d1 = new_w1.get_data(b"data1")
            d2 = w2.get_data(b"data2")
            return gatherResults([d1,d2], True)
        d.addCallback(_got_code)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            self.assertRaises(UsageError, w2.serialize) # too late
        d.addCallback(_done)
        return d

if sys.version_info[0] >= 3:
    Basic.skip = "twisted is not yet sufficiently ported to py3"
    # as of 15.4.0, Twisted is still missing:
    # * web.client.Agent (for all non-EventSource POSTs in transcribe.py)
    # * python.logfile (to allow daemonization of 'wormhole server')
