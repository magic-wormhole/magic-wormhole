from __future__ import print_function
import sys, json
from twisted.trial import unittest
from twisted.internet.defer import gatherResults, succeed
from ..twisted.transcribe import Wormhole, UsageError, ChannelManager
from ..twisted.eventsource_twisted import EventSourceParser
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
        d.addCallback(lambda _: self._channel.deallocate())
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
            self._relay_server.prune()
            self.failUnlessEqual(len(self._relay_server._apps), 1)
        d.addCallback(_not_yet)
        # but deallocating both will make the messages go away
        d.addCallback(lambda _: c1.deallocate())
        def _gone(_):
            self._relay_server.prune()
            self.failUnlessEqual(len(self._relay_server._apps), 0)
        d.addCallback(_gone)

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
        return d

class Basic(ServerBase, unittest.TestCase):

    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    def test_basic(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth(w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_done)
        return d

    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth(w1.send_data(b"data"), w2.send_data(b"data"))
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth(w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data")
            self.assertEqual(dataY, b"data")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_done)
        return d

    def test_interleaved(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth(w1.send_data(b"data1"), w2.get_data())
        d.addCallback(_got_code)
        def _sent(res):
            (_, dataY) = res
            self.assertEqual(dataY, b"data1")
            return self.doBoth(w1.get_data(), w2.send_data(b"data2"))
        d.addCallback(_sent)
        def _done(dl):
            (dataX, _) = dl
            self.assertEqual(dataX, b"data2")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_done)
        return d

    def test_fixed_code(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        d = self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        def _sent(res):
            return self.doBoth(w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_done)
        return d

    def test_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        d = self.doBoth(w1.send_data(b"data1", u"p1"),
                        w2.send_data(b"data2", u"p1"))
        d.addCallback(lambda _:
                      self.doBoth(w1.send_data(b"data3", u"p2"),
                                  w2.send_data(b"data4", u"p2")))
        d.addCallback(lambda _:
                      self.doBoth(w1.get_data(u"p2"),
                                  w2.get_data(u"p1")))
        def _got_1(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data4")
            self.assertEqual(dataY, b"data1")
            return self.doBoth(w1.get_data(u"p1"),
                               w2.get_data(u"p2"))
        d.addCallback(_got_1)
        def _got_2(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data3")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_got_2)
        return d

    def test_verifier(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code)
            return self.doBoth(w1.get_verifier(), w2.get_verifier())
        d.addCallback(_got_code)
        def _check_verifier(res):
            v1, v2 = res
            self.failUnlessEqual(type(v1), type(b""))
            self.failUnlessEqual(v1, v2)
            return self.doBoth(w1.send_data(b"data1"), w2.send_data(b"data2"))
        d.addCallback(_check_verifier)
        def _sent(res):
            return self.doBoth(w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual(dataX, b"data2")
            self.assertEqual(dataY, b"data1")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_done)
        return d

    def test_verifier_mismatch(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            w2.set_code(code+"not")
            return self.doBoth(w1.get_verifier(), w2.get_verifier())
        d.addCallback(_got_code)
        def _check_verifier(res):
            v1, v2 = res
            self.failUnlessEqual(type(v1), type(b""))
            self.failIfEqual(v1, v2)
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_check_verifier)
        return d

    def test_errors(self):
        w1 = Wormhole(APPID, self.relayurl)
        self.assertRaises(UsageError, w1.get_verifier)
        self.assertRaises(UsageError, w1.send_data, b"data")
        self.assertRaises(UsageError, w1.get_data)
        w1.set_code(u"123-purple-elephant")
        self.assertRaises(UsageError, w1.set_code, u"123-nope")
        self.assertRaises(UsageError, w1.get_code)
        w2 = Wormhole(APPID, self.relayurl)
        d = w2.get_code()
        self.assertRaises(UsageError, w2.get_code)
        def _got_code(code):
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_got_code)
        return d

    def test_repeat_phases(self):
        w1 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2 = Wormhole(APPID, self.relayurl)
        w2.set_code(u"123-purple-elephant")
        # we must let them establish a key before we can send data
        d = self.doBoth(w1.get_verifier(), w2.get_verifier())
        d.addCallback(lambda _: w1.send_data(b"data1", phase=u"1"))
        def _sent(res):
            # you can't send twice to the same phase
            self.assertRaises(UsageError, w1.send_data, b"data1", phase=u"1")
            # but you can send to a different one
            return w1.send_data(b"data2", phase=u"2")
        d.addCallback(_sent)
        d.addCallback(lambda _: w2.get_data(phase=u"1"))
        def _got1(res):
            self.failUnlessEqual(res, b"data1")
            # and you can't read twice from the same phase
            self.assertRaises(UsageError, w2.get_data, phase=u"1")
            # but you can read from a different one
            return w2.get_data(phase=u"2")
        d.addCallback(_got1)
        def _got2(res):
            self.failUnlessEqual(res, b"data2")
            return self.doBoth(w1.close(), w2.close())
        d.addCallback(_got2)
        return d

    def test_serialize(self):
        w1 = Wormhole(APPID, self.relayurl)
        self.assertRaises(UsageError, w1.serialize) # too early
        w2 = Wormhole(APPID, self.relayurl)
        d = w1.get_code()
        def _got_code(code):
            self.assertRaises(UsageError, w2.serialize) # too early
            w2.set_code(code)
            w2.serialize() # ok
            s = w1.serialize()
            self.assertEqual(type(s), type(""))
            unpacked = json.loads(s) # this is supposed to be JSON
            self.assertEqual(type(unpacked), dict)
            self.new_w1 = Wormhole.from_serialized(s)
            return self.doBoth(self.new_w1.send_data(b"data1"),
                               w2.send_data(b"data2"))
        d.addCallback(_got_code)
        def _sent(res):
            return self.doBoth(self.new_w1.get_data(), w2.get_data())
        d.addCallback(_sent)
        def _done(dl):
            (dataX, dataY) = dl
            self.assertEqual((dataX, dataY), (b"data2", b"data1"))
            self.assertRaises(UsageError, w2.serialize) # too late
            return gatherResults([w1.close(), w2.close(), self.new_w1.close()],
                                 True)
        d.addCallback(_done)
        return d

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

if sys.version_info[0] >= 3:
    Channel.skip = "twisted is not yet sufficiently ported to py3"
    Basic.skip = "twisted is not yet sufficiently ported to py3"
    EventSourceClient.skip = "twisted is not yet sufficiently ported to py3"
    # as of 15.4.0, Twisted is still missing:
    # * web.client.Agent (for all non-EventSource POSTs in transcribe.py)
    # * python.logfile (to allow daemonization of 'wormhole server')
