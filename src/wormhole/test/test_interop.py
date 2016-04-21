from __future__ import print_function
from twisted.trial import unittest
from twisted.internet.defer import gatherResults
from twisted.internet.threads import deferToThread
from ..twisted.transcribe import Wormhole as twisted_Wormhole
from ..blocking.transcribe import Wormhole as blocking_Wormhole
from .common import ServerBase

# make sure the two implementations (Twisted-style and blocking-style) can
# interoperate

APPID = u"appid"

class Basic(ServerBase, unittest.TestCase):

    def doBoth(self, call1, d2):
        f1 = call1[0]
        f1args = call1[1:]
        return gatherResults([deferToThread(f1, *f1args), d2], True)

    def test_twisted_to_blocking(self):
        tw = twisted_Wormhole(APPID, self.relayurl)
        bw = blocking_Wormhole(APPID, self.relayurl)
        d = tw.get_code()
        def _got_code(code):
            bw.set_code(code)
            return self.doBoth([bw.send_data, b"data2"], tw.send_data(b"data1"))
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth([bw.get_data], tw.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data1")
            self.assertEqual(dataY, b"data2")
            return self.doBoth([bw.close], tw.close())
        d.addCallback(_done)
        return d

    def test_blocking_to_twisted(self):
        bw = blocking_Wormhole(APPID, self.relayurl)
        tw = twisted_Wormhole(APPID, self.relayurl)
        d = deferToThread(bw.get_code)
        def _got_code(code):
            tw.set_code(code)
            return self.doBoth([bw.send_data, b"data1"], tw.send_data(b"data2"))
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth([bw.get_data], tw.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth([bw.close], tw.close())
        d.addCallback(_done)
        return d
