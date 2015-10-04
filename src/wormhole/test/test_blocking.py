import json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults
from twisted.internet.threads import deferToThread
from ..blocking.transcribe import Wormhole as BlockingWormhole, UsageError
from .common import ServerBase

class Blocking(ServerBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def doBoth(self, call1, call2):
        f1 = call1[0]
        f1args = call1[1:]
        f2 = call2[0]
        f2args = call2[1:]
        return gatherResults([deferToThread(f1, *f1args),
                              deferToThread(f2, *f2args)], True)

    def test_basic(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth([w1.send_data, b"data1"],
                               [w2.send_data, b"data2"])
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth([w1.get_data], [w2.get_data])
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_interleaved(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth([w1.send_data, b"data1"],
                               [w2.get_data])
        d.addCallback(_got_code)
        def _sent(res):
            (_, dataY) = res
            self.assertEqual(dataY, b"data1")
            return self.doBoth([w1.get_data], [w2.send_data, b"data2"])
        d.addCallback(_sent)
        def _done(dl):
            (dataX, _) = dl
            self.assertEqual(dataX, b"data2")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_fixed_code(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        d = self.doBoth([w1.send_data, b"data1"], [w2.send_data, b"data2"])
        def _sent(res):
            return self.doBoth([w1.get_data], [w2.get_data])
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_verifier(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth([w1.get_verifier], [w2.get_verifier])
        d.addCallback(_got_code)
        def _check_verifier(res):
            v1, v2 = res
            self.failUnlessEqual(type(v1), type(b""))
            self.failUnlessEqual(v1, v2)
            return self.doBoth([w1.send_data, b"data1"],
                               [w2.send_data, b"data2"])
        d.addCallback(_check_verifier)
        def _sent(res):
            return self.doBoth([w1.get_data], [w2.get_data])
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_verifier_mismatch(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code+"not")
            return self.doBoth([w1.get_verifier], [w2.get_verifier])
        d.addCallback(_got_code)
        def _check_verifier(res):
            v1, v2 = res
            self.failUnlessEqual(type(v1), type(b""))
            self.failIfEqual(v1, v2)
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_check_verifier)
        return d

    def test_errors(self):
        appid = b"appid"
        w1 = BlockingWormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.get_data)
        self.assertRaises(UsageError, w1.send_data, b"data")
        w1.set_code("123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, "123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = BlockingWormhole(appid, self.relayurl)
        d = deferToThread(w2.get_code)
        def _done(code):
            self.assertRaises(UsageError, w2.get_code)
            return self.doBoth([w1.close], [w2.close])
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
            self.new_w1 = BlockingWormhole.from_serialized(s)
            return self.doBoth([self.new_w1.send_data, b"data1"],
                               [w2.send_data, b"data2"])
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth(self.new_w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            self.assertRaises(UsageError, w2.serialize) # too late
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d
    test_serialize.skip = "not yet implemented for the blocking flavor"
