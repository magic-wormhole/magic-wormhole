from __future__ import print_function
import json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults, succeed, inlineCallbacks
from txwormhole.transcribe import (Wormhole, UsageError, ChannelManager,
                                   WrongPasswordError)
from txwormhole.eventsource import EventSourceParser
from .common import ServerBase

APPID = u"appid"

class Channel(ServerBase, unittest.TestCase):
    def ignore(self, welcome):
        pass

    def test_allocate(self):
        cm = ChannelManager(self.relayurl, APPID, u"side", self.ignore)
        d = cm.list_channels()
        def _got_channels(channels):
            self.failUnlessEqual(channels, [])
        d.addCallback(_got_channels)
        d.addCallback(lambda _: cm.allocate())
        def _allocated(channelid):
            self.failUnlessEqual(type(channelid), int)
            self._channelid = channelid
        d.addCallback(_allocated)
        d.addCallback(lambda _: cm.connect(self._channelid))
        def _connected(c):
            self._channel = c
        d.addCallback(_connected)
        d.addCallback(lambda _: self._channel.deallocate(u"happy"))
        d.addCallback(lambda _: cm.shutdown())
        return d

    def test_messages(self):
        cm1 = ChannelManager(self.relayurl, APPID, u"side1", self.ignore)
        cm2 = ChannelManager(self.relayurl, APPID, u"side2", self.ignore)
        c1 = cm1.connect(1)
        c2 = cm2.connect(1)

        d = succeed(None)
        d.addCallback(lambda _: c1.send(u"phase1", b"msg1"))
        d.addCallback(lambda _: c2.get(u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1"))
        d.addCallback(lambda _: c2.send(u"phase1", b"msg2"))
        d.addCallback(lambda _: c1.get(u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg2"))
        # it's legal to fetch a phase multiple times, should be idempotent
        d.addCallback(lambda _: c1.get(u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg2"))
        # deallocating one side is not enough to destroy the channel
        d.addCallback(lambda _: c2.deallocate())
        def _not_yet(_):
            self._rendezvous.prune()
            self.failUnlessEqual(len(self._rendezvous._apps), 1)
        d.addCallback(_not_yet)
        # but deallocating both will make the messages go away
        d.addCallback(lambda _: c1.deallocate(u"sad"))
        def _gone(_):
            self._rendezvous.prune()
            self.failUnlessEqual(len(self._rendezvous._apps), 0)
        d.addCallback(_gone)

        d.addCallback(lambda _: cm1.shutdown())
        d.addCallback(lambda _: cm2.shutdown())

        return d

    def test_get_multiple_phases(self):
        cm1 = ChannelManager(self.relayurl, APPID, u"side1", self.ignore)
        cm2 = ChannelManager(self.relayurl, APPID, u"side2", self.ignore)
        c1 = cm1.connect(1)
        c2 = cm2.connect(1)

        self.failUnlessRaises(TypeError, c2.get_first_of, u"phase1")
        self.failUnlessRaises(TypeError, c2.get_first_of, [u"phase1", 7])

        d = succeed(None)
        d.addCallback(lambda _: c1.send(u"phase1", b"msg1"))

        d.addCallback(lambda _: c2.get_first_of([u"phase1", u"phase2"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))
        d.addCallback(lambda _: c2.get_first_of([u"phase2", u"phase1"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))

        d.addCallback(lambda _: c1.send(u"phase2", b"msg2"))
        d.addCallback(lambda _: c2.get(u"phase2"))

        # if both are present, it should prefer the first one we asked for
        d.addCallback(lambda _: c2.get_first_of([u"phase1", u"phase2"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))
        d.addCallback(lambda _: c2.get_first_of([u"phase2", u"phase1"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase2", b"msg2")))

        d.addCallback(lambda _: cm1.shutdown())
        d.addCallback(lambda _: cm2.shutdown())

        return d

    def test_appid_independence(self):
        APPID_A = u"appid_A"
        APPID_B = u"appid_B"
        cm1a = ChannelManager(self.relayurl, APPID_A, u"side1", self.ignore)
        cm2a = ChannelManager(self.relayurl, APPID_A, u"side2", self.ignore)
        c1a = cm1a.connect(1)
        c2a = cm2a.connect(1)
        cm1b = ChannelManager(self.relayurl, APPID_B, u"side1", self.ignore)
        cm2b = ChannelManager(self.relayurl, APPID_B, u"side2", self.ignore)
        c1b = cm1b.connect(1)
        c2b = cm2b.connect(1)

        d = succeed(None)
        d.addCallback(lambda _: c1a.send(u"phase1", b"msg1a"))
        d.addCallback(lambda _: c1b.send(u"phase1", b"msg1b"))
        d.addCallback(lambda _: c2a.get(u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1a"))
        d.addCallback(lambda _: c2b.get(u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1b"))

        d.addCallback(lambda _: cm1a.shutdown())
        d.addCallback(lambda _: cm2a.shutdown())
        d.addCallback(lambda _: cm1b.shutdown())
        d.addCallback(lambda _: cm2b.shutdown())
        return d

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
        yield self.doBoth(w1.send_data(b"data1"),
                          self.assertFailure(w2.get_data(), WrongPasswordError))

        # and now w1 should have enough information to throw too
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
    def test_verifier_mismatch(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        # we must disable confirmation messages, else the wormholes will
        # figure out the mismatch by themselves and throw WrongPasswordError.
        w1._send_confirm = w2._send_confirm = False
        code = yield w1.get_code()
        w2.set_code(code+"not")
        res = yield self.doBoth(w1.get_verifier(), w2.get_verifier())
        v1, v2 = res
        self.failUnlessEqual(type(v1), type(b""))
        self.failIfEqual(v1, v2)
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


data1 = b"""\
event: welcome
data: one and a
data: two
data:.

data: three

: this line is ignored
event: e2
: this line is ignored too
i am a dataless field name
data: four

"""

class FakeTransport:
    disconnecting = False

class EventSourceClient(unittest.TestCase):
    def test_parser(self):
        events = []
        p = EventSourceParser(lambda t,d: events.append((t,d)))
        p.transport = FakeTransport()
        p.dataReceived(data1)
        self.failUnlessEqual(events,
                             [(u"welcome", u"one and a\ntwo\n."),
                              (u"message", u"three"),
                              (u"e2", u"four"),
                              ])

# new py3 support in 15.5.0: web.client.Agent, w.c.downloadPage, twistd

# However trying 'wormhole server start' with py3/twisted-15.5.0 throws an
# error in t.i._twistd_unix.UnixApplicationRunner.postApplication, it calls
# os.write with str, not bytes. This file does not cover that test (testing
# daemonization is hard).
