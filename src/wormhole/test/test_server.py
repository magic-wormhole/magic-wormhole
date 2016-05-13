from __future__ import print_function
import json, itertools
from binascii import hexlify
from twisted.trial import unittest
from twisted.internet import protocol, reactor, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.endpoints import clientFromString, connectProtocol
from twisted.web.client import getPage, Agent, readBody
from autobahn.twisted import websocket
from .. import __version__
from .common import ServerBase
from ..server import rendezvous, transit_server

class Reachable(ServerBase, unittest.TestCase):

    def test_getPage(self):
        # client.getPage requires bytes URL, returns bytes
        url = self.relayurl.replace("wormhole-relay/", "").encode("ascii")
        d = getPage(url)
        def _got(res):
            self.failUnlessEqual(res, b"Wormhole Relay\n")
        d.addCallback(_got)
        return d

    def test_agent(self):
        url = self.relayurl.replace("wormhole-relay/", "").encode("ascii")
        agent = Agent(reactor)
        d = agent.request(b"GET", url)
        def _check(resp):
            self.failUnlessEqual(resp.code, 200)
            return readBody(resp)
        d.addCallback(_check)
        def _got(res):
            self.failUnlessEqual(res, b"Wormhole Relay\n")
        d.addCallback(_got)
        return d

def strip_message(msg):
    m2 = msg.copy()
    m2.pop("id", None)
    m2.pop("server_rx", None)
    return m2

def strip_messages(messages):
    return [strip_message(m) for m in messages]

class WSClient(websocket.WebSocketClientProtocol):
    def __init__(self):
        websocket.WebSocketClientProtocol.__init__(self)
        self.events = []
        self.errors = []
        self.d = None
        self.ping_counter = itertools.count(0)
    def onOpen(self):
        self.factory.d.callback(self)
    def onMessage(self, payload, isBinary):
        assert not isBinary
        event = json.loads(payload.decode("utf-8"))
        if event["type"] == "error":
            self.errors.append(event)
        if self.d:
            assert not self.events
            d,self.d = self.d,None
            d.callback(event)
            return
        self.events.append(event)

    def next_event(self):
        assert not self.d
        if self.events:
            event = self.events.pop(0)
            return defer.succeed(event)
        self.d = defer.Deferred()
        return self.d

    @inlineCallbacks
    def next_non_ack(self):
        while True:
            m = yield self.next_event()
            if m["type"] != "ack":
                returnValue(m)

    def strip_acks(self):
        self.events = [e for e in self.events if e["type"] != u"ack"]

    def send(self, mtype, **kwargs):
        kwargs["type"] = mtype
        payload = json.dumps(kwargs).encode("utf-8")
        self.sendMessage(payload, False)

    @inlineCallbacks
    def sync(self):
        ping = next(self.ping_counter)
        self.send("ping", ping=ping)
        # queue all messages until the pong, then put them back
        old_events = []
        while True:
            ev = yield self.next_event()
            if ev["type"] == "pong" and ev["pong"] == ping:
                self.events = old_events + self.events
                returnValue(None)
            old_events.append(ev)

class WSFactory(websocket.WebSocketClientFactory):
    protocol = WSClient

class WSClientSync(unittest.TestCase):
    # make sure my 'sync' method actually works

    @inlineCallbacks
    def test_sync(self):
        sent = []
        c = WSClient()
        def _send(mtype, **kwargs):
            sent.append( (mtype, kwargs) )
        c.send = _send
        def add(mtype, **kwargs):
            kwargs["type"] = mtype
            c.onMessage(json.dumps(kwargs).encode("utf-8"), False)
        # no queued messages
        sunc = []
        d = c.sync()
        d.addBoth(sunc.append)
        self.assertEqual(sent, [("ping", {"ping": 0})])
        self.assertEqual(sunc, [])
        add("pong", pong=0)
        yield d
        self.assertEqual(c.events, [])

        # one,two,ping,pong
        add("one")
        add("two", two=2)
        sunc = []
        d = c.sync()
        d.addBoth(sunc.append)
        add("pong", pong=1)
        yield d
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "one")
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "two")
        self.assertEqual(c.events, [])

        # one,ping,two,pong
        add("one")
        sunc = []
        d = c.sync()
        d.addBoth(sunc.append)
        add("two", two=2)
        add("pong", pong=2)
        yield d
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "one")
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "two")
        self.assertEqual(c.events, [])

        # ping,one,two,pong
        sunc = []
        d = c.sync()
        d.addBoth(sunc.append)
        add("one")
        add("two", two=2)
        add("pong", pong=3)
        yield d
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "one")
        m = yield c.next_non_ack()
        self.assertEqual(m["type"], "two")
        self.assertEqual(c.events, [])



class WebSocketAPI(ServerBase, unittest.TestCase):
    def setUp(self):
        self._clients = []
        return ServerBase.setUp(self)

    def tearDown(self):
        for c in self._clients:
            c.transport.loseConnection()
        return ServerBase.tearDown(self)

    @inlineCallbacks
    def make_client(self):
        f = WSFactory(self.rdv_ws_url)
        f.d = defer.Deferred()
        reactor.connectTCP("127.0.0.1", self.rdv_ws_port, f)
        c = yield f.d
        self._clients.append(c)
        returnValue(c)

    def check_welcome(self, data):
        self.failUnlessIn("welcome", data)
        self.failUnlessEqual(data["welcome"], {"current_version": __version__})

    @inlineCallbacks
    def test_welcome(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        self.assertEqual(self._rendezvous._apps, {})

    @inlineCallbacks
    def test_claim(self):
        r = self._rendezvous.get_app(u"appid")
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"claim", channelid=u"1")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set(u"1"))

        c1.send(u"claim", channelid=u"2")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set([u"1", u"2"]))

        c1.send(u"claim", channelid=u"72aoqnnnbj7r2")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set([u"1", u"2", u"72aoqnnnbj7r2"]))

        c1.send(u"release", channelid=u"2")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set([u"1", u"72aoqnnnbj7r2"]))

        c1.send(u"release", channelid=u"1")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set([u"72aoqnnnbj7r2"]))


    @inlineCallbacks
    def test_allocate_1(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        yield c1.sync()
        self.assertEqual(list(self._rendezvous._apps.keys()), [u"appid"])
        app = self._rendezvous.get_app(u"appid")
        self.assertEqual(app.get_claimed(), set())
        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [])

        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        self.failUnlessIsInstance(cid, type(u""))
        self.assertEqual(app.get_claimed(), set([cid]))
        channel = app.get_channel(cid)
        self.assertEqual(channel.get_messages(), [])

        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [cid])

        c1.send(u"release", channelid=cid)
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"released")
        self.assertEqual(msg["status"], u"deleted")
        self.assertEqual(app.get_claimed(), set())

        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [])

    @inlineCallbacks
    def test_allocate_2(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        yield c1.sync()
        app = self._rendezvous.get_app(u"appid")
        self.assertEqual(app.get_claimed(), set())
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        self.failUnlessIsInstance(cid, type(u""))
        self.assertEqual(app.get_claimed(), set([cid]))
        channel = app.get_channel(cid)
        self.assertEqual(channel.get_messages(), [])

        # second caller increases the number of known sides to 2
        c2 = yield self.make_client()
        msg = yield c2.next_non_ack()
        self.check_welcome(msg)
        c2.send(u"bind", appid=u"appid", side=u"side-2")
        c2.send(u"claim", channelid=cid)
        c2.send(u"add", channelid=cid, phase="1", body="")
        yield c2.sync()

        self.assertEqual(app.get_claimed(), set([cid]))
        self.assertEqual(strip_messages(channel.get_messages()),
                         [{"phase": "1", "body": ""}])

        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [cid])

        c2.send(u"list")
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [cid])

        c1.send(u"release", channelid=cid)
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"released")
        self.assertEqual(msg["status"], u"waiting")

        c2.send(u"release", channelid=cid)
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], u"released")
        self.assertEqual(msg["status"], u"deleted")

        c2.send(u"list")
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [])

    @inlineCallbacks
    def test_allocate_and_claim(self):
        r = self._rendezvous.get_app(u"appid")
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        c1.send(u"claim", channelid=cid)
        yield c1.sync()
        # there should no error
        self.assertEqual(c1.errors, [])
        self.assertEqual(r.get_claimed(), set([cid]))

        # but trying to allocate twice is an error
        c1.send(u"allocate")
        yield c1.sync()
        self.assertEqual(len(c1.errors), 1)
        self.assertEqual(c1.errors[0]["error"],
                         "You already allocated one channel, don't be greedy")
        self.assertEqual(r.get_claimed(), set([cid]))

    @inlineCallbacks
    def test_allocate_and_claim_two(self):
        r = self._rendezvous.get_app(u"appid")
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        c1.send(u"claim", channelid=cid)
        yield c1.sync()
        # there should no error
        self.assertEqual(c1.errors, [])

        c1.send(u"claim", channelid=u"other")
        yield c1.sync()
        self.assertEqual(c1.errors, [])
        self.assertEqual(r.get_claimed(), set([cid, u"other"]))

        c1.send(u"release", channelid=cid)
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set([u"other"]))
        c1.send(u"release", channelid="other")
        yield c1.sync()
        self.assertEqual(r.get_claimed(), set())

    @inlineCallbacks
    def test_message(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        app = self._rendezvous.get_app(u"appid")
        channel = app.get_channel(cid)
        self.assertEqual(channel.get_messages(), [])

        c1.send(u"watch", channelid=cid)
        yield c1.sync()
        self.assertEqual(len(channel._listeners), 1)
        c1.strip_acks()
        self.assertEqual(c1.events, [])

        c1.send(u"add", channelid=cid, phase="1", body="msg1A")
        yield c1.sync()
        c1.strip_acks()
        self.assertEqual(strip_messages(channel.get_messages()),
                         [{"phase": "1", "body": "msg1A"}])
        self.assertEqual(len(c1.events), 1) # echo should be sent right away
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "1", "body": "msg1A"})
        self.assertIn("server_tx", msg)
        self.assertIsInstance(msg["server_tx"], float)

        c1.send(u"add", channelid=cid, phase="1", body="msg1B")
        c1.send(u"add", channelid=cid, phase="2", body="msg2A")

        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "1", "body": "msg1B"})

        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "2", "body": "msg2A"})

        self.assertEqual(strip_messages(channel.get_messages()), [
            {"phase": "1", "body": "msg1A"},
            {"phase": "1", "body": "msg1B"},
            {"phase": "2", "body": "msg2A"},
            ])

        # second client should see everything
        c2 = yield self.make_client()
        msg = yield c2.next_non_ack()
        self.check_welcome(msg)
        c2.send(u"bind", appid=u"appid", side=u"side")
        c2.send(u"claim", channelid=cid)
        # 'watch' triggers delivery of old messages, in temporal order
        c2.send(u"watch", channelid=cid)

        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "1", "body": "msg1A"})

        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "1", "body": "msg1B"})

        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "2", "body": "msg2A"})

        # adding a duplicate is not an error, and clients will ignore it
        c1.send(u"add", channelid=cid, phase="2", body="msg2A")

        # the duplicate message *does* get stored, and delivered
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "2", "body": "msg2A"})


class Summary(unittest.TestCase):
    def test_summarize(self):
        c = rendezvous.Channel(None, None, None, None, False, None, None)
        A = rendezvous.CLAIM
        D = rendezvous.RELEASE

        messages = [{"server_rx": 1, "side": "a", "phase": A}]
        self.failUnlessEqual(c._summarize(messages, 2),
                             (1, "lonely", 1, None))

        messages = [{"server_rx": 1, "side": "a", "phase": A},
                    {"server_rx": 2, "side": "a", "phase": D, "body": "lonely"},
                    ]
        self.failUnlessEqual(c._summarize(messages, 3),
                             (1, "lonely", 2, None))

        messages = [{"server_rx": 1, "side": "a", "phase": A},
                    {"server_rx": 2, "side": "b", "phase": A},
                    {"server_rx": 3, "side": "c", "phase": A},
                    ]
        self.failUnlessEqual(c._summarize(messages, 4),
                             (1, "crowded", 3, None))

        base = [{"server_rx": 1, "side": "a", "phase": A},
                {"server_rx": 2, "side": "a", "phase": "pake", "body": "msg1"},
                {"server_rx": 10, "side": "b", "phase": "pake", "body": "msg2"},
                {"server_rx": 11, "side": "b", "phase": "data", "body": "msg3"},
                {"server_rx": 20, "side": "a", "phase": "data", "body": "msg4"},
                ]
        def make_moods(A_mood, B_mood):
            return base + [
                {"server_rx": 21, "side": "a", "phase": D, "body": A_mood},
                {"server_rx": 30, "side": "b", "phase": D, "body": B_mood},
                ]

        self.failUnlessEqual(c._summarize(make_moods("happy", "happy"), 41),
                             (1, "happy", 40, 9))

        self.failUnlessEqual(c._summarize(make_moods("scary", "happy"), 41),
                             (1, "scary", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods("happy", "scary"), 41),
                             (1, "scary", 40, 9))

        self.failUnlessEqual(c._summarize(make_moods("lonely", "happy"), 41),
                             (1, "lonely", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods("happy", "lonely"), 41),
                             (1, "lonely", 40, 9))

        self.failUnlessEqual(c._summarize(make_moods("errory", "happy"), 41),
                             (1, "errory", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods("happy", "errory"), 41),
                             (1, "errory", 40, 9))

        # scary trumps other moods
        self.failUnlessEqual(c._summarize(make_moods("scary", "lonely"), 41),
                             (1, "scary", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods("scary", "errory"), 41),
                             (1, "scary", 40, 9))

        # older clients don't send a mood
        self.failUnlessEqual(c._summarize(make_moods(None, None), 41),
                             (1, "quiet", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods(None, "happy"), 41),
                             (1, "quiet", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods(None, "happy"), 41),
                             (1, "quiet", 40, 9))
        self.failUnlessEqual(c._summarize(make_moods(None, "scary"), 41),
                             (1, "scary", 40, 9))

class Accumulator(protocol.Protocol):
    def __init__(self):
        self.data = b""
        self.count = 0
        self._wait = None
    def waitForBytes(self, more):
        assert self._wait is None
        self.count = more
        self._wait = defer.Deferred()
        self._check_done()
        return self._wait
    def dataReceived(self, data):
        self.data = self.data + data
        self._check_done()
    def _check_done(self):
        if self._wait and len(self.data) >= self.count:
            d = self._wait
            self._wait = None
            d.callback(self)
    def connectionLost(self, why):
        if self._wait:
            self._wait.errback(RuntimeError("closed"))

class Transit(ServerBase, unittest.TestCase):
    def test_blur_size(self):
        blur = transit_server.blur_size
        self.failUnlessEqual(blur(0), 0)
        self.failUnlessEqual(blur(1), 10e3)
        self.failUnlessEqual(blur(10e3), 10e3)
        self.failUnlessEqual(blur(10e3+1), 20e3)
        self.failUnlessEqual(blur(15e3), 20e3)
        self.failUnlessEqual(blur(20e3), 20e3)
        self.failUnlessEqual(blur(1e6), 1e6)
        self.failUnlessEqual(blur(1e6+1), 2e6)
        self.failUnlessEqual(blur(1.5e6), 2e6)
        self.failUnlessEqual(blur(2e6), 2e6)
        self.failUnlessEqual(blur(900e6), 900e6)
        self.failUnlessEqual(blur(1000e6), 1000e6)
        self.failUnlessEqual(blur(1050e6), 1100e6)
        self.failUnlessEqual(blur(1100e6), 1100e6)
        self.failUnlessEqual(blur(1150e6), 1200e6)

    @defer.inlineCallbacks
    def test_basic(self):
        ep = clientFromString(reactor, self.transit)
        a1 = yield connectProtocol(ep, Accumulator())
        a2 = yield connectProtocol(ep, Accumulator())

        token1 = b"\x00"*32
        a1.transport.write(b"please relay " + hexlify(token1) + b"\n")
        a2.transport.write(b"please relay " + hexlify(token1) + b"\n")

        # a correct handshake yields an ack, after which we can send
        exp = b"ok\n"
        yield a1.waitForBytes(len(exp))
        self.assertEqual(a1.data, exp)
        s1 = b"data1"
        a1.transport.write(s1)

        exp = b"ok\n"
        yield a2.waitForBytes(len(exp))
        self.assertEqual(a2.data, exp)

        # all data they sent after the handshake should be given to us
        exp = b"ok\n"+s1
        yield a2.waitForBytes(len(exp))
        self.assertEqual(a2.data, exp)

        a1.transport.loseConnection()
        a2.transport.loseConnection()

    @defer.inlineCallbacks
    def test_bad_handshake(self):
        ep = clientFromString(reactor, self.transit)
        a1 = yield connectProtocol(ep, Accumulator())

        token1 = b"\x00"*32
        # the server waits for the exact number of bytes in the expected
        # handshake message. to trigger "bad handshake", we must match.
        a1.transport.write(b"please DELAY " + hexlify(token1) + b"\n")

        exp = b"bad handshake\n"
        yield a1.waitForBytes(len(exp))
        self.assertEqual(a1.data, exp)

        a1.transport.loseConnection()

    @defer.inlineCallbacks
    def test_impatience(self):
        ep = clientFromString(reactor, self.transit)
        a1 = yield connectProtocol(ep, Accumulator())

        token1 = b"\x00"*32
        # sending too many bytes is impatience.
        a1.transport.write(b"please RELAY NOWNOW " + hexlify(token1) + b"\n")

        exp = b"impatient\n"
        yield a1.waitForBytes(len(exp))
        self.assertEqual(a1.data, exp)

        a1.transport.loseConnection()
