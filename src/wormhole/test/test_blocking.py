from __future__ import print_function
import json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults, succeed
from twisted.internet.threads import deferToThread
from ..blocking.transcribe import (Wormhole, UsageError, ChannelManager,
                                   WrongPasswordError)
from ..blocking.eventsource import EventSourceFollower
from .common import ServerBase

APPID = u"appid"

class Channel(ServerBase, unittest.TestCase):
    def ignore(self, welcome):
        pass

    def test_allocate(self):
        cm = ChannelManager(self.relayurl, APPID, u"side", self.ignore)
        d = deferToThread(cm.list_channels)
        def _got_channels(channels):
            self.failUnlessEqual(channels, [])
        d.addCallback(_got_channels)
        d.addCallback(lambda _: deferToThread(cm.allocate))
        def _allocated(channelid):
            self.failUnlessEqual(type(channelid), int)
            self._channelid = channelid
        d.addCallback(_allocated)
        d.addCallback(lambda _: deferToThread(cm.connect, self._channelid))
        def _connected(c):
            self._channel = c
        d.addCallback(_connected)
        d.addCallback(lambda _: deferToThread(self._channel.deallocate,
                                              u"happy"))
        return d

    def test_messages(self):
        cm1 = ChannelManager(self.relayurl, APPID, u"side1", self.ignore)
        cm2 = ChannelManager(self.relayurl, APPID, u"side2", self.ignore)
        c1 = cm1.connect(1)
        c2 = cm2.connect(1)

        d = succeed(None)
        d.addCallback(lambda _: deferToThread(c1.send, u"phase1", b"msg1"))
        d.addCallback(lambda _: deferToThread(c2.get, u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1"))
        d.addCallback(lambda _: deferToThread(c2.send, u"phase1", b"msg2"))
        d.addCallback(lambda _: deferToThread(c1.get, u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg2"))
        # it's legal to fetch a phase multiple times, should be idempotent
        d.addCallback(lambda _: deferToThread(c1.get, u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg2"))
        # deallocating one side is not enough to destroy the channel
        d.addCallback(lambda _: deferToThread(c2.deallocate))
        def _not_yet(_):
            self._rendezvous.prune()
            self.failUnlessEqual(len(self._rendezvous._apps), 1)
        d.addCallback(_not_yet)
        # but deallocating both will make the messages go away
        d.addCallback(lambda _: deferToThread(c1.deallocate, u"sad"))
        def _gone(_):
            self._rendezvous.prune()
            self.failUnlessEqual(len(self._rendezvous._apps), 0)
        d.addCallback(_gone)

        return d

    def test_get_multiple_phases(self):
        cm1 = ChannelManager(self.relayurl, APPID, u"side1", self.ignore)
        cm2 = ChannelManager(self.relayurl, APPID, u"side2", self.ignore)
        c1 = cm1.connect(1)
        c2 = cm2.connect(1)

        self.failUnlessRaises(TypeError, c2.get_first_of, u"phase1")
        self.failUnlessRaises(TypeError, c2.get_first_of, [u"phase1", 7])

        d = succeed(None)
        d.addCallback(lambda _: deferToThread(c1.send, u"phase1", b"msg1"))

        d.addCallback(lambda _: deferToThread(c2.get_first_of, [u"phase1",
                                                                u"phase2"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))
        d.addCallback(lambda _: deferToThread(c2.get_first_of, [u"phase2",
                                                                u"phase1"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))

        d.addCallback(lambda _: deferToThread(c1.send, u"phase2", b"msg2"))
        d.addCallback(lambda _: deferToThread(c2.get, u"phase2"))

        # if both are present, it should prefer the first one we asked for
        d.addCallback(lambda _: deferToThread(c2.get_first_of, [u"phase1",
                                                                u"phase2"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase1", b"msg1")))
        d.addCallback(lambda _: deferToThread(c2.get_first_of, [u"phase2",
                                                                u"phase1"]))
        d.addCallback(lambda phase_and_body:
                      self.failUnlessEqual(phase_and_body,
                                           (u"phase2", b"msg2")))

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
        d.addCallback(lambda _: deferToThread(c1a.send, u"phase1", b"msg1a"))
        d.addCallback(lambda _: deferToThread(c1b.send, u"phase1", b"msg1b"))
        d.addCallback(lambda _: deferToThread(c2a.get, u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1a"))
        d.addCallback(lambda _: deferToThread(c2b.get, u"phase1"))
        d.addCallback(lambda msg: self.failUnlessEqual(msg, b"msg1b"))
        return d

class _DoBothMixin:
    def doBoth(self, call1, call2):
        f1 = call1[0]
        f1args = call1[1:]
        f2 = call2[0]
        f2args = call2[1:]
        return gatherResults([deferToThread(f1, *f1args),
                              deferToThread(f2, *f2args)], True)

class Blocking(_DoBothMixin, ServerBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def test_basic(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
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

    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth([w1.send_data, b"data"],
                               [w2.send_data, b"data"])
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth([w1.get_data], [w2.get_data])
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data")
            self.assertEqual(dataY, b"data")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_interleaved(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
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
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
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

    def test_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        d = self.doBoth([w1.send_data, b"data1", u"p1"],
                        [w2.send_data, b"data2", u"p1"])
        d.addCallback(lambda _:
                      self.doBoth([w1.send_data, b"data3", u"p2"],
                                  [w2.send_data, b"data4", u"p2"]))
        d.addCallback(lambda _:
                      self.doBoth([w1.get_data, u"p2"],
                                  [w2.get_data, u"p1"]))
        def _got_1(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data4")
            self.assertEqual(dataY, b"data1")
            return self.doBoth([w1.get_data, u"p1"],
                               [w2.get_data, u"p2"])
        d.addCallback(_got_1)
        def _got_2(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data3")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_got_2)
        return d

    def test_wrong_password(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)

        # make sure we can detect WrongPasswordError even if one side only
        # does get_data() and not send_data(), like "wormhole receive" does
        d = deferToThread(w1.get_code)
        d.addCallback(lambda code: w2.set_code(code+"not"))

        # w2 can't throw WrongPasswordError until it sees a CONFIRM message,
        # and w1 won't send CONFIRM until it sees a PAKE message, which w2
        # won't send until we call get_data. So we need both sides to be
        # running at the same time for this test.
        def _w1_sends():
            w1.send_data(b"data1")
        def _w2_gets():
            self.assertRaises(WrongPasswordError, w2.get_data)
        d.addCallback(lambda _: self.doBoth([_w1_sends], [_w2_gets]))

        # and now w1 should have enough information to throw too
        d.addCallback(lambda _: deferToThread(self.assertRaises,
                                              WrongPasswordError, w1.get_data))
        def _done(_):
            # both sides are closed automatically upon error, but it's still
            # legal to call .close(), and should be idempotent
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_no_confirm(self):
        # newer versions (which check confirmations) should will work with
        # older versions (that don't send confirmations)
        w1 = Wormhole(APPID, self.relayurl)
        w1._send_confirm = False
        w2 = Wormhole(APPID, self.relayurl)

        d = deferToThread(w1.get_code)
        d.addCallback(lambda code: w2.set_code(code))
        d.addCallback(lambda _: self.doBoth([w1.send_data, b"data1"],
                                            [w2.get_data]))
        d.addCallback(lambda dl: self.assertEqual(dl[1], b"data1"))
        d.addCallback(lambda _: self.doBoth([w1.get_data],
                                            [w2.send_data, b"data2"]))
        d.addCallback(lambda dl: self.assertEqual(dl[0], b"data2"))
        d.addCallback(lambda _: self.doBoth([w1.close], [w2.close]))
        return d

    def test_verifier(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
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
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
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
        w1 = Wormhole(APPID, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.get_data)
        self.assertRaises(UsageError, w1.send_data, b"data")
        w1.set_code(u"123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, u"123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = Wormhole(APPID, self.relayurl)
        d = deferToThread(w2.get_code)
        def _done(code):
            self.assertRaises(UsageError, w2.get_code)
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_done)
        return d

    def test_repeat_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2 = Wormhole(APPID, self.relayurl)
        w2.set_code(u"123-purple-elephant")
        # we must let them establish a key before we can send data
        d = self.doBoth([w1.get_verifier], [w2.get_verifier])
        d.addCallback(lambda _:
                      deferToThread(w1.send_data, b"data1", phase=u"1"))
        def _sent(res):
            # underscore-prefixed phases are reserved
            self.assertRaises(UsageError, w1.send_data, b"data1", phase=u"_1")
            self.assertRaises(UsageError, w1.get_data, phase=u"_1")
            # you can't send twice to the same phase
            self.assertRaises(UsageError, w1.send_data, b"data1", phase=u"1")
            # but you can send to a different one
            return deferToThread(w1.send_data, b"data2", phase=u"2")
        d.addCallback(_sent)
        d.addCallback(lambda _: deferToThread(w2.get_data, phase=u"1"))
        def _got1(res):
            self.failUnlessEqual(res, b"data1")
            # and you can't read twice from the same phase
            self.assertRaises(UsageError, w2.get_data, phase=u"1")
            # but you can read from a different one
            return deferToThread(w2.get_data, phase=u"2")
        d.addCallback(_got1)
        def _got2(res):
            self.failUnlessEqual(res, b"data2")
            return self.doBoth([w1.close], [w2.close])
        d.addCallback(_got2)
        return d

    def test_serialize(self):
        w1 = Wormhole(APPID, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = Wormhole(APPID, self.relayurl)
        d = deferToThread(w1.get_code)
        def _got_code(code):
            self.assertRaises(UsageError, w2.serialize) # too early
            w2.set_code(code)
            w2.serialize() # ok
            s = w1.serialize()
            self.assertEqual(type(s), type(""))
            unpacked = json.loads(s) # this is supposed to be JSON
            self.assertEqual(type(unpacked), dict)
            self.new_w1 = Wormhole.from_serialized(s)
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

data1 = u"""\
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

class NoNetworkESF(EventSourceFollower):
    def __init__(self, text):
        self._lines_iter = iter(text.splitlines())

class EventSourceClient(unittest.TestCase):
    def test_parser(self):
        events = []
        f = NoNetworkESF(data1)
        events = list(f.iter_events())
        self.failUnlessEqual(events,
                             [(u"welcome", u"one and a\ntwo\n."),
                              (u"message", u"three"),
                              (u"e2", u"four"),
                              ])
