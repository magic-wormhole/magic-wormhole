import json
from twisted.trial import unittest
from twisted.internet import defer
from twisted.application import service
from ..servers.relay import RelayServer
from ..twisted.transcribe import SymmetricWormhole, UsageError
from ..twisted.util import allocate_ports
from .. import __version__
#from twisted.python import log
#import sys
#log.startLogging(sys.stdout)

class Basic(unittest.TestCase):
    def setUp(self):
        self.sp = service.MultiService()
        self.sp.startService()
        d = allocate_ports()
        def _got_ports(ports):
            relayport, transitport = ports
            s = RelayServer("tcp:%d:interface=127.0.0.1" % relayport,
                            "tcp:%s:interface=127.0.0.1" % transitport,
                            __version__)
            s.setServiceParent(self.sp)
            self.relayurl = "http://127.0.0.1:%d/wormhole-relay/" % relayport
            self.transit = "tcp:127.0.0.1:%d" % transitport
        d.addCallback(_got_ports)
        return d

    def tearDown(self):
        return self.sp.stopService()

    def test_basic(self):
        appid = "appid"
        w1 = SymmetricWormhole(appid, self.relayurl)
        w2 = SymmetricWormhole(appid, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            d1 = w1.get_data("data1")
            d2 = w2.get_data("data2")
            return defer.DeferredList([d1,d2], fireOnOneErrback=False)
        d.addCallback(_got_code)
        def _done(dl):
            ((success1, dataX), (success2, dataY)) = dl
            r1,r2 = dl
            self.assertTrue(success1)
            self.assertTrue(success2)
            self.assertEqual(dataX, "data2")
            self.assertEqual(dataY, "data1")
        d.addCallback(_done)
        return d

    def test_fixed_code(self):
        appid = "appid"
        w1 = SymmetricWormhole(appid, self.relayurl)
        w2 = SymmetricWormhole(appid, self.relayurl)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        d1 = w1.get_data("data1")
        d2 = w2.get_data("data2")
        d = defer.DeferredList([d1,d2], fireOnOneErrback=False)
        def _done(dl):
            ((success1, dataX), (success2, dataY)) = dl
            r1,r2 = dl
            self.assertTrue(success1)
            self.assertTrue(success2)
            self.assertEqual(dataX, "data2")
            self.assertEqual(dataY, "data1")
        d.addCallback(_done)
        return d

    def test_errors(self):
        appid = "appid"
        w1 = SymmetricWormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.get_data, "data")
        w1.set_code("123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, "123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = SymmetricWormhole(appid, self.relayurl)
        d = w2.get_code()
        self.assertRaises(UsageError, w2.get_code)
        return d

    def test_serialize(self):
        appid = "appid"
        w1 = SymmetricWormhole(appid, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = SymmetricWormhole(appid, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            self.assertRaises(UsageError, w2.serialize) # too early
            w2.set_code(code)
            w2.serialize() # ok
            s = w1.serialize()
            self.assertEqual(type(s), type(""))
            unpacked = json.loads(s) # this is supposed to be JSON
            self.assertEqual(type(unpacked), dict)
            new_w1 = SymmetricWormhole.from_serialized(s)
            d1 = new_w1.get_data("data1")
            d2 = w2.get_data("data2")
            return defer.DeferredList([d1,d2], fireOnOneErrback=False)
        d.addCallback(_got_code)
        def _done(dl):
            ((success1, dataX), (success2, dataY)) = dl
            r1,r2 = dl
            self.assertTrue(success1)
            self.assertTrue(success2)
            self.assertEqual(dataX, "data2")
            self.assertEqual(dataY, "data1")
            self.assertRaises(UsageError, w2.serialize) # too late
        d.addCallback(_done)
        return d
