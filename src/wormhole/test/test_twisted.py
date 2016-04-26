from __future__ import print_function
import json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults, inlineCallbacks
from ..twisted.transcribe import Wormhole, UsageError, WrongPasswordError
from .common import ServerBase

APPID = u"appid"

class Basic(ServerBase, unittest.TestCase):

    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_basic(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        yield self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        dl = yield self.doBoth(w1.get_data(), w2.get_data())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        yield self.doBoth(w1.send_data(b"data"), w2.send_data(b"data"))
        dl = yield self.doBoth(w1.get_data(), w2.get_data())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data")
        self.assertEqual(dataY, b"data")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_interleaved(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        res = yield self.doBoth(w1.send_data(b"data1"), w2.get_data())
        (_, dataY) = res
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get_data(), w2.send_data(b"data2"))
        (dataX, _) = dl
        self.assertEqual(dataX, b"data2")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_fixed_code(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        yield self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        dl = yield self.doBoth(w1.get_data(), w2.get_data())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield self.doBoth(w1.close(), w2.close())


    @inlineCallbacks
    def test_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        yield self.doBoth(w1.send_data(b"data1", u"p1"),
                          w2.send_data(b"data2", u"p1"))
        yield self.doBoth(w1.send_data(b"data3", u"p2"),
                          w2.send_data(b"data4", u"p2"))
        dl = yield self.doBoth(w1.get_data(u"p2"),
                               w2.get_data(u"p1"))
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data4")
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get_data(u"p1"),
                               w2.get_data(u"p2"))
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data3")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_wrong_password(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code+"not")

        # w2 can't throw WrongPasswordError until it sees a CONFIRM message,
        # and w1 won't send CONFIRM until it sees a PAKE message, which w2
        # won't send until we call get_data. So we need both sides to be
        # running at the same time for this test.
        d1 = w1.send_data(b"data1")
        # at this point, w1 should be waiting for w2.PAKE

        yield self.assertFailure(w2.get_data(), WrongPasswordError)
        # * w2 will send w2.PAKE, wait for (and get) w1.PAKE, compute a key,
        #   send w2.CONFIRM, then wait for w1.DATA.
        # * w1 will get w2.PAKE, compute a key, send w1.CONFIRM.
        # * w1 might also get w2.CONFIRM, and may notice the error before it
        #   sends w1.CONFIRM, in which case the wait=True will signal an
        #   error inside _get_master_key() (inside send_data), and d1 will
        #   errback.
        #   * but w1 might not see w2.CONFIRM yet, in which case it won't
        #     errback until we do w1.get_data()
        # * w2 gets w1.CONFIRM, notices the error, records it.
        # * w2 (waiting for w1.DATA) wakes up, sees the error, throws
        # * meanwhile w1 finishes sending its data. w2.CONFIRM may or may not
        #   have arrived by then
        try:
            yield d1
        except WrongPasswordError:
            pass

        # When we ask w1 to get_data(), one of two things might happen:
        # * if w2.CONFIRM arrived already, it will have recorded the error.
        #   When w1.get_data() sleeps (waiting for w2.DATA), we'll notice the
        #   error before sleeping, and throw WrongPasswordError
        # * if w2.CONFIRM hasn't arrived yet, we'll sleep. When w2.CONFIRM
        #   arrives, we notice and record the error, and wake up, and throw

        # Note that we didn't do w2.send_data(), so we're hoping that w1 will
        # have enough information to detect the error before it sleeps
        # (waiting for w2.DATA). Checking for the error both before sleeping
        # and after waking up makes this happen.

        # so now w1 should have enough information to throw too
        yield self.assertFailure(w1.get_data(), WrongPasswordError)

        # both sides are closed automatically upon error, but it's still
        # legal to call .close(), and should be idempotent
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_no_confirm(self):
        # newer versions (which check confirmations) should will work with
        # older versions (that don't send confirmations)
        w1 = Wormhole(APPID, self.relayurl)
        w1._send_confirm = False
        w2 = Wormhole(APPID, self.relayurl)

        code = yield w1.get_code()
        w2.set_code(code)
        dl = yield self.doBoth(w1.send_data(b"data1"), w2.get_data())
        self.assertEqual(dl[1], b"data1")
        dl = yield self.doBoth(w1.get_data(), w2.send_data(b"data2"))
        self.assertEqual(dl[0], b"data2")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_verifier(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        res = yield self.doBoth(w1.get_verifier(), w2.get_verifier())
        v1, v2 = res
        self.failUnlessEqual(type(v1), type(b""))
        self.failUnlessEqual(v1, v2)
        yield self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        dl = yield self.doBoth(w1.get_data(), w2.get_data())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_errors(self):
        w1 = Wormhole(APPID, self.relayurl)
        yield self.assertFailure(w1.get_verifier(), UsageError)
        yield self.assertFailure(w1.send_data(b"data"), UsageError)
        yield self.assertFailure(w1.get_data(), UsageError)
        w1.set_code(u"123-purple-elephant")
        yield self.assertRaises(UsageError, w1.set_code, u"123-nope")
        yield self.assertFailure(w1.get_code(), UsageError)
        w2 = Wormhole(APPID, self.relayurl)
        yield w2.get_code()
        yield self.assertFailure(w2.get_code(), UsageError)
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_repeat_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2 = Wormhole(APPID, self.relayurl)
        w2.set_code(u"123-purple-elephant")
        # we must let them establish a key before we can send data
        yield self.doBoth(w1.get_verifier(), w2.get_verifier())
        yield w1.send_data(b"data1", phase=u"1")
        # underscore-prefixed phases are reserved
        yield self.assertFailure(w1.send_data(b"data1", phase=u"_1"),
                                 UsageError)
        yield self.assertFailure(w1.get_data(phase=u"_1"), UsageError)
        # you can't send twice to the same phase
        yield self.assertFailure(w1.send_data(b"data1", phase=u"1"),
                                 UsageError)
        # but you can send to a different one
        yield w1.send_data(b"data2", phase=u"2")
        res = yield w2.get_data(phase=u"1")
        self.failUnlessEqual(res, b"data1")
        # and you can't read twice from the same phase
        yield self.assertFailure(w2.get_data(phase=u"1"), UsageError)
        # but you can read from a different one
        res = yield w2.get_data(phase=u"2")
        self.failUnlessEqual(res, b"data2")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_serialize(self):
        w1 = Wormhole(APPID, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        self.assertRaises(UsageError, w2.serialize) # too early
        w2.set_code(code)
        w2.serialize() # ok
        s = w1.serialize()
        self.assertEqual(type(s), type(""))
        unpacked = json.loads(s) # this is supposed to be JSON
        self.assertEqual(type(unpacked), dict)

        self.new_w1 = Wormhole.from_serialized(s)
        yield self.doBoth(self.new_w1.send_data(b"data1"),
                          w2.send_data(b"data2"))
        dl = yield self.doBoth(self.new_w1.get_data(), w2.get_data())
        (dataX, dataY) = dl
        self.assertEqual((dataX, dataY), (b"data2", b"data1"))
        self.assertRaises(UsageError, w2.serialize) # too late
        yield gatherResults([w1.close(), w2.close(), self.new_w1.close()],
                            True)

