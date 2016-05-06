from __future__ import print_function
import json, itertools
from binascii import hexlify
import requests
from six.moves.urllib_parse import urlencode
from twisted.trial import unittest
from twisted.internet import protocol, reactor, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.threads import deferToThread
from twisted.internet.endpoints import clientFromString, connectProtocol
from twisted.web.client import getPage, Agent, readBody
from autobahn.twisted import websocket
from .. import __version__
from .common import ServerBase
from ..server import rendezvous, transit_server
from ..twisted.eventsource import EventSource

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

    def test_requests(self):
        # requests requires bytes URL, returns unicode
        url = self.relayurl.replace("wormhole-relay/", "")
        def _get(url):
            r = requests.get(url)
            r.raise_for_status()
            return r.text
        d = deferToThread(_get, url)
        def _got(res):
            self.failUnlessEqual(res, "Wormhole Relay\n")
        d.addCallback(_got)
        return d

def unjson(data):
    return json.loads(data.decode("utf-8"))

def strip_message(msg):
    m2 = msg.copy()
    m2.pop("id", None)
    m2.pop("server_rx", None)
    return m2

def strip_messages(messages):
    return [strip_message(m) for m in messages]

class WebAPI(ServerBase, unittest.TestCase):
    def build_url(self, path, appid, channelid):
        url = self.relayurl+path
        queryargs = []
        if appid:
            queryargs.append(("appid", appid))
        if channelid:
            queryargs.append(("channelid", channelid))
        if queryargs:
            url += "?" + urlencode(queryargs)
        return url

    def get(self, path, appid=None, channelid=None):
        url = self.build_url(path, appid, channelid)
        d = getPage(url.encode("ascii"))
        d.addCallback(unjson)
        return d

    def post(self, path, data):
        url = self.relayurl+path
        d = getPage(url.encode("ascii"), method=b"POST",
                    postdata=json.dumps(data).encode("utf-8"))
        d.addCallback(unjson)
        return d

    def check_welcome(self, data):
        self.failUnlessIn("welcome", data)
        self.failUnlessEqual(data["welcome"], {"current_version": __version__})

    def test_allocate_1(self):
        d = self.get("list", "app1")
        def _check_list_1(data):
            self.check_welcome(data)
            self.failUnlessEqual(data["channelids"], [])
        d.addCallback(_check_list_1)

        d.addCallback(lambda _: self.post("allocate", {"appid": "app1",
                                                       "side": "abc"}))
        def _allocated(data):
            data.pop("sent", None)
            self.failUnlessEqual(set(data.keys()),
                                 set(["welcome", "channelid"]))
            self.failUnlessIsInstance(data["channelid"], int)
            self.cid = data["channelid"]
        d.addCallback(_allocated)

        d.addCallback(lambda _: self.get("list", "app1"))
        def _check_list_2(data):
            self.failUnlessEqual(data["channelids"], [self.cid])
        d.addCallback(_check_list_2)

        d.addCallback(lambda _: self.post("deallocate",
                                          {"appid": "app1",
                                           "channelid": str(self.cid),
                                           "side": "abc"}))
        def _check_deallocate(res):
            self.failUnlessEqual(res["status"], "deleted")
        d.addCallback(_check_deallocate)

        d.addCallback(lambda _: self.get("list", "app1"))
        def _check_list_3(data):
            self.failUnlessEqual(data["channelids"], [])
        d.addCallback(_check_list_3)

        return d

    def test_allocate_2(self):
        d = self.post("allocate", {"appid": "app1", "side": "abc"})
        def _allocated(data):
            self.cid = data["channelid"]
        d.addCallback(_allocated)

        # second caller increases the number of known sides to 2
        d.addCallback(lambda _: self.post("add",
                                          {"appid": "app1",
                                           "channelid": str(self.cid),
                                           "side": "def",
                                           "phase": "1",
                                           "body": ""}))

        d.addCallback(lambda _: self.get("list", "app1"))
        d.addCallback(lambda data:
                      self.failUnlessEqual(data["channelids"], [self.cid]))

        d.addCallback(lambda _: self.post("deallocate",
                                          {"appid": "app1",
                                           "channelid": str(self.cid),
                                           "side": "abc"}))
        d.addCallback(lambda res:
                      self.failUnlessEqual(res["status"], "waiting"))

        d.addCallback(lambda _: self.post("deallocate",
                                          {"appid": "app1",
                                           "channelid": str(self.cid),
                                           "side": "NOT"}))
        d.addCallback(lambda res:
                      self.failUnlessEqual(res["status"], "waiting"))

        d.addCallback(lambda _: self.post("deallocate",
                                          {"appid": "app1",
                                           "channelid": str(self.cid),
                                           "side": "def"}))
        d.addCallback(lambda res:
                      self.failUnlessEqual(res["status"], "deleted"))

        d.addCallback(lambda _: self.get("list", "app1"))
        d.addCallback(lambda data:
                      self.failUnlessEqual(data["channelids"], []))

        return d

    UPGRADE_ERROR = "Sorry, you must upgrade your client to use this server."
    def test_old_allocate(self):
        # 0.4.0 used "POST /allocate/SIDE".
        # 0.5.0 replaced it with "POST /allocate".
        # test that an old client gets a useful error message, not a 404.
        d = self.post("allocate/abc", {})
        def _check(data):
            self.failUnlessEqual(data["welcome"]["error"], self.UPGRADE_ERROR)
        d.addCallback(_check)
        return d

    def test_old_list(self):
        # 0.4.0 used "GET /list".
        # 0.5.0 replaced it with "GET /list?appid="
        d = self.get("list", {}) # no appid
        def _check(data):
            self.failUnlessEqual(data["welcome"]["error"], self.UPGRADE_ERROR)
        d.addCallback(_check)
        return d

    def test_old_post(self):
        # 0.4.0 used "POST /CID/SIDE/post/MSGNUM"
        # 0.5.0 replaced it with "POST /add (json body)"
        d = self.post("1/abc/post/pake", {})
        def _check(data):
            self.failUnlessEqual(data["welcome"]["error"], self.UPGRADE_ERROR)
        d.addCallback(_check)
        return d

    def add_message(self, message, side="abc", phase="1"):
        return self.post("add",
                         {"appid": "app1",
                          "channelid": str(self.cid),
                         "side": side,
                          "phase": phase,
                          "body": message})

    def parse_messages(self, messages):
        out = set()
        for m in messages:
            self.failUnlessEqual(sorted(m.keys()), sorted(["phase", "body"]))
            self.failUnlessIsInstance(m["phase"], type(u""))
            self.failUnlessIsInstance(m["body"], type(u""))
            out.add( (m["phase"], m["body"]) )
        return out

    def check_messages(self, one, two):
        # Comparing lists-of-dicts is non-trivial in python3 because we can
        # neither sort them (dicts are uncomparable), nor turn them into sets
        # (dicts are unhashable). This is close enough.
        self.failUnlessEqual(len(one), len(two), (one,two))
        for d in one:
            self.failUnlessIn(d, two)

    def test_message(self):
        # exercise POST /add
        d = self.post("allocate", {"appid": "app1", "side": "abc"})
        def _allocated(data):
            self.cid = data["channelid"]
        d.addCallback(_allocated)

        d.addCallback(lambda _: self.add_message("msg1A"))
        def _check1(data):
            self.check_welcome(data)
            self.failUnlessEqual(strip_messages(data["messages"]),
                                 [{"phase": "1", "body": "msg1A"}])
        d.addCallback(_check1)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check1)
        d.addCallback(lambda _: self.add_message("msg1B", side="def"))
        def _check2(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(strip_messages(data["messages"])),
                                 set([("1", "msg1A"),
                                      ("1", "msg1B")]))
        d.addCallback(_check2)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check2)

        # adding a duplicate message is not an error, is ignored by clients
        d.addCallback(lambda _: self.add_message("msg1B", side="def"))
        def _check3(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(strip_messages(data["messages"])),
                                 set([("1", "msg1A"),
                                      ("1", "msg1B")]))
        d.addCallback(_check3)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check3)

        d.addCallback(lambda _: self.add_message("msg2A", side="abc",
                                                 phase="2"))
        def _check4(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(strip_messages(data["messages"])),
                                 set([("1", "msg1A"),
                                      ("1", "msg1B"),
                                      ("2", "msg2A"),
                                      ]))
        d.addCallback(_check4)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check4)

        return d

    def test_watch_message(self):
        # exercise GET /get (the EventSource version)
        # this API is scheduled to be removed after 0.6.0
        return self._do_watch("get")

    def test_watch(self):
        # exercise GET /watch (the EventSource version)
        return self._do_watch("watch")

    def _do_watch(self, endpoint_name):
        d = self.post("allocate", {"appid": "app1", "side": "abc"})
        def _allocated(data):
            self.cid = data["channelid"]
            url = self.build_url(endpoint_name, "app1", self.cid)
            self.o = OneEventAtATime(url, parser=json.loads)
            return self.o.wait_for_connection()
        d.addCallback(_allocated)
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_welcome(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "welcome")
            self.failUnlessEqual(data, {"current_version": __version__})
        d.addCallback(_check_welcome)
        d.addCallback(lambda _: self.add_message("msg1A"))
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_msg1(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "message")
            data.pop("sent", None)
            self.failUnlessEqual(strip_message(data),
                                 {"phase": "1", "body": "msg1A"})
        d.addCallback(_check_msg1)

        d.addCallback(lambda _: self.add_message("msg1B"))
        d.addCallback(lambda _: self.add_message("msg2A", phase="2"))
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_msg2(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "message")
            data.pop("sent", None)
            self.failUnlessEqual(strip_message(data),
                                 {"phase": "1", "body": "msg1B"})
        d.addCallback(_check_msg2)
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_msg3(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "message")
            data.pop("sent", None)
            self.failUnlessEqual(strip_message(data),
                                 {"phase": "2", "body": "msg2A"})
        d.addCallback(_check_msg3)

        d.addCallback(lambda _: self.o.close())
        d.addCallback(lambda _: self.o.wait_for_disconnection())
        return d

class OneEventAtATime:
    def __init__(self, url, parser=lambda e: e):
        self.parser = parser
        self.d = None
        self._connected = False
        self.connected_d = defer.Deferred()
        self.disconnected_d = defer.Deferred()
        self.events = []
        self.es = EventSource(url, self.handler, when_connected=self.connected)
        d = self.es.start()
        d.addBoth(self.disconnected)

    def close(self):
        self.es.cancel()

    def wait_for_next_event(self):
        assert not self.d
        if self.events:
            event = self.events.pop(0)
            return defer.succeed(event)
        self.d = defer.Deferred()
        return self.d

    def handler(self, eventtype, data):
        event = (eventtype, self.parser(data))
        if self.d:
            assert not self.events
            d,self.d = self.d,None
            d.callback(event)
            return
        self.events.append(event)

    def wait_for_connection(self):
        return self.connected_d
    def connected(self):
        self._connected = True
        self.connected_d.callback(None)

    def wait_for_disconnection(self):
        return self.disconnected_d
    def disconnected(self, why):
        if not self._connected:
            self.connected_d.errback(why)
        self.disconnected_d.callback((why,))

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
    def test_allocate_1(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        yield c1.sync()
        self.assertEqual(list(self._rendezvous._apps.keys()), [u"appid"])
        app = self._rendezvous.get_app(u"appid")
        self.assertEqual(app.get_allocated(), set())
        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [])

        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        self.failUnlessIsInstance(cid, int)
        self.assertEqual(app.get_allocated(), set([cid]))
        channel = app.get_channel(cid)
        self.assertEqual(channel.get_messages(), [])

        c1.send(u"list")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [cid])

        c1.send(u"deallocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"deallocated")
        self.assertEqual(msg["status"], u"deleted")
        self.assertEqual(app.get_allocated(), set())

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
        self.assertEqual(app.get_allocated(), set())
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        self.failUnlessIsInstance(cid, int)
        self.assertEqual(app.get_allocated(), set([cid]))
        channel = app.get_channel(cid)
        self.assertEqual(channel.get_messages(), [])

        # second caller increases the number of known sides to 2
        c2 = yield self.make_client()
        msg = yield c2.next_non_ack()
        self.check_welcome(msg)
        c2.send(u"bind", appid=u"appid", side=u"side-2")
        c2.send(u"claim", channelid=cid)
        c2.send(u"add", phase="1", body="")
        yield c2.sync()

        self.assertEqual(app.get_allocated(), set([cid]))
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

        c1.send(u"deallocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"deallocated")
        self.assertEqual(msg["status"], u"waiting")

        c2.send(u"deallocate")
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], u"deallocated")
        self.assertEqual(msg["status"], u"deleted")

        c2.send(u"list")
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], u"channelids")
        self.assertEqual(msg["channelids"], [])

    @inlineCallbacks
    def test_allocate_and_claim(self):
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

    @inlineCallbacks
    def test_allocate_and_claim_different(self):
        c1 = yield self.make_client()
        msg = yield c1.next_non_ack()
        self.check_welcome(msg)
        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"allocate")
        msg = yield c1.next_non_ack()
        self.assertEqual(msg["type"], u"allocated")
        cid = msg["channelid"]
        c1.send(u"claim", channelid=cid+1)
        yield c1.sync()
        # that should signal an error
        self.assertEqual(len(c1.errors), 1, c1.errors)
        msg = c1.errors[0]
        self.assertEqual(msg["type"], "error")
        self.assertEqual(msg["error"], "Already bound to channelid %d" % cid)
        self.assertEqual(msg["orig"], {"type": "claim", "channelid": cid+1})

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

        c1.send(u"watch")
        yield c1.sync()
        self.assertEqual(len(channel._listeners), 1)
        c1.strip_acks()
        self.assertEqual(c1.events, [])

        c1.send(u"add", phase="1", body="msg1A")
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

        c1.send(u"add", phase="1", body="msg1B")
        c1.send(u"add", phase="2", body="msg2A")

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
        c2.send(u"watch")

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
        c1.send(u"add", phase="2", body="msg2A")

        # the duplicate message *does* get stored, and delivered
        msg = yield c2.next_non_ack()
        self.assertEqual(msg["type"], "message")
        self.assertEqual(strip_message(msg["message"]),
                         {"phase": "2", "body": "msg2A"})


class Summary(unittest.TestCase):
    def test_summarize(self):
        c = rendezvous.Channel(None, None, None, None, False, None, None)
        A = rendezvous.ALLOCATE
        D = rendezvous.DEALLOCATE

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
