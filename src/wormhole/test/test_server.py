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
from ..server.rendezvous import Usage

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

class Server(ServerBase, unittest.TestCase):
    def test_apps(self):
        app1 = self._rendezvous.get_app(u"appid1")
        self.assertIdentical(app1, self._rendezvous.get_app(u"appid1"))
        app2 = self._rendezvous.get_app(u"appid2")
        self.assertNotIdentical(app1, app2)

    def test_nameplate_allocation(self):
        app = self._rendezvous.get_app(u"appid")
        nids = set()
        # this takes a second, and claims all the short-numbered nameplates
        def add():
            nameplate_id = app.allocate_nameplate(u"side1", 0)
            self.assertEqual(type(nameplate_id), type(u""))
            nid = int(nameplate_id)
            nids.add(nid)
        for i in range(9): add()
        self.assertNotIn(0, nids)
        self.assertEqual(set(range(1,10)), nids)

        for i in range(100-10): add()
        self.assertEqual(len(nids), 99)
        self.assertEqual(set(range(1,100)), nids)

        for i in range(1000-100): add()
        self.assertEqual(len(nids), 999)
        self.assertEqual(set(range(1,1000)), nids)

        add()
        self.assertEqual(len(nids), 1000)
        biggest = max(nids)
        self.assert_(1000 <= biggest < 1000000, biggest)

    def _nameplate(self, app, nameplate_id):
        return app._db.execute("SELECT * FROM `nameplates`"
                               " WHERE `app_id`='appid' AND `id`=?",
                               (nameplate_id,)).fetchone()

    def test_nameplate(self):
        app = self._rendezvous.get_app(u"appid")
        nameplate_id = app.allocate_nameplate(u"side1", 0)
        self.assertEqual(type(nameplate_id), type(u""))
        nid = int(nameplate_id)
        self.assert_(0 < nid < 10, nid)
        self.assertEqual(app.get_nameplate_ids(), set([nameplate_id]))
        # allocate also does a claim
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["crowded"], False)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], None)

        mailbox_id = app.claim_nameplate(nameplate_id, u"side1", 1)
        self.assertEqual(type(mailbox_id), type(u""))
        # duplicate claims by the same side are combined
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], None)

        mailbox_id2 = app.claim_nameplate(nameplate_id, u"side1", 2)
        self.assertEqual(mailbox_id, mailbox_id2)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], None)

        # claim by the second side is new
        mailbox_id3 = app.claim_nameplate(nameplate_id, u"side2", 3)
        self.assertEqual(mailbox_id, mailbox_id3)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")
        self.assertEqual(row["crowded"], False)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 3)

        # a third claim marks the nameplate as "crowded", but leaves the two
        # existing claims alone
        self.assertRaises(rendezvous.CrowdedError,
                          app.claim_nameplate, nameplate_id, u"side3", 0)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")
        self.assertEqual(row["crowded"], True)

        # releasing a non-existent nameplate is ignored
        app.release_nameplate(nameplate_id+u"not", u"side4", 0)

        # releasing a side that never claimed the nameplate is ignored
        app.release_nameplate(nameplate_id, u"side4", 0)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")

        # releasing one side leaves the second claim
        app.release_nameplate(nameplate_id, u"side1", 5)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side2")
        self.assertEqual(row["side2"], None)

        # releasing one side multiple times is ignored
        app.release_nameplate(nameplate_id, u"side1", 5)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row["side1"], u"side2")
        self.assertEqual(row["side2"], None)

        # releasing the second side frees the nameplate, and adds usage
        app.release_nameplate(nameplate_id, u"side2", 6)
        row = self._nameplate(app, nameplate_id)
        self.assertEqual(row, None)
        usage = app._db.execute("SELECT * FROM `nameplate_usage`").fetchone()
        self.assertEqual(usage["app_id"], u"appid")
        self.assertEqual(usage["started"], 0)
        self.assertEqual(usage["waiting_time"], 3)
        self.assertEqual(usage["total_time"], 6)
        self.assertEqual(usage["result"], u"crowded")


    def _mailbox(self, app, mailbox_id):
        return app._db.execute("SELECT * FROM `mailboxes`"
                               " WHERE `app_id`='appid' AND `id`=?",
                               (mailbox_id,)).fetchone()

    def test_mailbox(self):
        app = self._rendezvous.get_app(u"appid")
        mailbox_id = u"mid"
        m1 = app.open_mailbox(mailbox_id, u"side1", 0)

        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["crowded"], False)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], None)

        # opening the same mailbox twice, by the same side, gets the same
        # object
        self.assertIdentical(m1, app.open_mailbox(mailbox_id, u"side1", 1))
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["crowded"], False)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], None)

        # opening a second side gets the same object, and adds a new claim
        self.assertIdentical(m1, app.open_mailbox(mailbox_id, u"side2", 2))
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")
        self.assertEqual(row["crowded"], False)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 2)

        # a third open marks it as crowded
        self.assertRaises(rendezvous.CrowdedError,
                          app.open_mailbox, mailbox_id, u"side3", 3)
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")
        self.assertEqual(row["crowded"], True)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 2)

        # closing a side that never claimed the mailbox is ignored
        m1.close(u"side4", u"mood", 4)
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side1")
        self.assertEqual(row["side2"], u"side2")
        self.assertEqual(row["crowded"], True)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 2)

        # closing one side leaves the second claim
        m1.close(u"side1", u"mood", 5)
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side2")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["crowded"], True)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 2)

        # closing one side multiple is ignored
        m1.close(u"side1", u"mood", 6)
        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row["side1"], u"side2")
        self.assertEqual(row["side2"], None)
        self.assertEqual(row["crowded"], True)
        self.assertEqual(row["started"], 0)
        self.assertEqual(row["second"], 2)

        l1 = []; stop1 = []; stop1_f = lambda: stop1.append(True)
        m1.add_listener("handle1", l1.append, stop1_f)

        # closing the second side frees the mailbox, and adds usage
        m1.close(u"side2", u"mood", 7)
        self.assertEqual(stop1, [True])

        row = self._mailbox(app, mailbox_id)
        self.assertEqual(row, None)
        usage = app._db.execute("SELECT * FROM `mailbox_usage`").fetchone()
        self.assertEqual(usage["app_id"], u"appid")
        self.assertEqual(usage["started"], 0)
        self.assertEqual(usage["waiting_time"], 2)
        self.assertEqual(usage["total_time"], 7)
        self.assertEqual(usage["result"], u"crowded")

    def _messages(self, app):
        c = app._db.execute("SELECT * FROM `messages`"
                            " WHERE `app_id`='appid' AND `mailbox_id`='mid'")
        return c.fetchall()

    def test_messages(self):
        app = self._rendezvous.get_app(u"appid")
        mailbox_id = u"mid"
        m1 = app.open_mailbox(mailbox_id, u"side1", 0)
        m1.add_message(u"side1", u"phase", u"body", 1, u"msgid")
        msgs = self._messages(app)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["body"], u"body")

        l1 = []; stop1 = []; stop1_f = lambda: stop1.append(True)
        l2 = []; stop2 = []; stop2_f = lambda: stop2.append(True)
        old = m1.add_listener("handle1", l1.append, stop1_f)
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["body"], u"body")

        m1.add_message(u"side1", u"phase2", u"body2", 1, u"msgid")
        self.assertEqual(len(l1), 1)
        self.assertEqual(l1[0]["body"], u"body2")
        old = m1.add_listener("handle2", l2.append, stop2_f)
        self.assertEqual(len(old), 2)

        m1.add_message(u"side1", u"phase3", u"body3", 1, u"msgid")
        self.assertEqual(len(l1), 2)
        self.assertEqual(l1[-1]["body"], u"body3")
        self.assertEqual(len(l2), 1)
        self.assertEqual(l2[-1]["body"], u"body3")

        m1.remove_listener("handle1")

        m1.add_message(u"side1", u"phase4", u"body4", 1, u"msgid")
        self.assertEqual(len(l1), 2)
        self.assertEqual(l1[-1]["body"], u"body3")
        self.assertEqual(len(l2), 2)
        self.assertEqual(l2[-1]["body"], u"body4")

        m1._shutdown()
        self.assertEqual(stop1, [])
        self.assertEqual(stop2, [True])

        # message adds are not idempotent: clients filter duplicates
        m1.add_message(u"side1", u"phase", u"body", 1, u"msgid")
        msgs = self._messages(app)
        self.assertEqual(len(msgs), 5)
        self.assertEqual(msgs[-1]["body"], u"body")


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

    def send_notype(self, **kwargs):
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
    def test_bind(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()

        c1.send(u"bind", appid=u"appid") # missing side=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"bind requires 'side'")

        c1.send(u"bind", side=u"side") # missing appid=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"bind requires 'appid'")

        c1.send(u"bind", appid=u"appid", side=u"side")
        yield c1.sync()
        self.assertEqual(self._rendezvous._apps.keys(), [u"appid"])

        c1.send(u"bind", appid=u"appid", side=u"side") # duplicate
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"already bound")

        c1.send_notype(other="misc") # missing 'type'
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"missing 'type'")

        c1.send("___unknown") # unknown type
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"unknown type")

        c1.send("ping") # missing 'ping'
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"ping requires 'ping'")

    @inlineCallbacks
    def test_list(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()

        c1.send(u"list") # too early, must bind first
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"must bind first")

        c1.send(u"bind", appid=u"appid", side=u"side")
        c1.send(u"list")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"nameplates")
        self.assertEqual(m[u"nameplates"], [])

        app = self._rendezvous.get_app(u"appid")
        nameplate_id1 = app.allocate_nameplate(u"side", 0)
        app.claim_nameplate(u"np2", u"side", 0)

        c1.send(u"list")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"nameplates")
        self.assertEqual(set(m[u"nameplates"]), set([nameplate_id1, u"np2"]))

    @inlineCallbacks
    def test_allocate(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()

        c1.send(u"allocate") # too early, must bind first
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"must bind first")

        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")
        c1.send(u"allocate")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"nameplate")
        nameplate_id = m[u"nameplate"]

        nids = app.get_nameplate_ids()
        self.assertEqual(len(nids), 1)
        self.assertEqual(nameplate_id, list(nids)[0])

        c1.send(u"allocate")
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"],
                         u"you already allocated one, don't be greedy")

        c1.send(u"claim", nameplate=nameplate_id) # allocate+claim is ok
        yield c1.sync()
        row = app._db.execute("SELECT * FROM `nameplates`"
                              " WHERE `app_id`='appid' AND `id`=?",
                              (nameplate_id,)).fetchone()
        self.assertEqual(row["side1"], u"side")
        self.assertEqual(row["side2"], None)

    @inlineCallbacks
    def test_claim(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")

        c1.send(u"claim") # missing nameplate=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"claim requires 'nameplate'")

        c1.send(u"claim", nameplate=u"np1")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"mailbox")
        mailbox_id = m[u"mailbox"]
        self.assertEqual(type(mailbox_id), type(u""))

        nids = app.get_nameplate_ids()
        self.assertEqual(len(nids), 1)
        self.assertEqual(u"np1", list(nids)[0])

        # claiming a nameplate will assign a random mailbox id, but won't
        # create the mailbox itself
        mailboxes = app._db.execute("SELECT * FROM `mailboxes`"
                                    " WHERE `app_id`='appid'").fetchall()
        self.assertEqual(len(mailboxes), 0)

    @inlineCallbacks
    def test_claim_crowded(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")

        app.claim_nameplate(u"np1", u"side1", 0)
        app.claim_nameplate(u"np1", u"side2", 0)

        # the third claim will signal crowding
        c1.send(u"claim", nameplate=u"np1")
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"crowded")

    @inlineCallbacks
    def test_release(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")

        app.claim_nameplate(u"np1", u"side2", 0)

        c1.send(u"release") # didn't do claim first
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"],
                         u"must claim a nameplate before releasing it")

        c1.send(u"claim", nameplate=u"np1")
        yield c1.next_non_ack()

        c1.send(u"release")
        yield c1.sync()

        row = app._db.execute("SELECT * FROM `nameplates`"
                              " WHERE `app_id`='appid' AND `id`='np1'").fetchone()
        self.assertEqual(row["side1"], u"side2")
        self.assertEqual(row["side2"], None)

        c1.send(u"release") # no longer claimed
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"],
                         u"must claim a nameplate before releasing it")

    @inlineCallbacks
    def test_open(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")

        c1.send(u"open") # missing mailbox=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"open requires 'mailbox'")

        mb1 = app.open_mailbox(u"mb1", u"side2", 0)
        mb1.add_message(u"side2", u"phase", u"body", 0, u"msgid")

        c1.send(u"open", mailbox=u"mb1")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"message")
        self.assertEqual(m[u"message"][u"body"], u"body")

        mb1.add_message(u"side2", u"phase2", u"body2", 0, u"msgid")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"message")
        self.assertEqual(m[u"message"][u"body"], u"body2")

        c1.send(u"open", mailbox=u"mb1")
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"you already have a mailbox open")

    @inlineCallbacks
    def test_add(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")
        mb1 = app.open_mailbox(u"mb1", u"side2", 0)
        l1 = []; stop1 = []; stop1_f = lambda: stop1.append(True)
        mb1.add_listener("handle1", l1.append, stop1_f)

        c1.send(u"add") # didn't open first
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"must open mailbox before adding")

        c1.send(u"open", mailbox=u"mb1")

        c1.send(u"add", body=u"body") # missing phase=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"missing 'phase'")

        c1.send(u"add", phase=u"phase") # missing body=
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"missing 'body'")

        c1.send(u"add", phase=u"phase", body=u"body")
        m = yield c1.next_non_ack() # echoed back
        self.assertEqual(m[u"type"], u"message")
        self.assertEqual(m[u"message"][u"body"], u"body")

        self.assertEqual(len(l1), 1)
        self.assertEqual(l1[0][u"body"], u"body")

    @inlineCallbacks
    def test_close(self):
        c1 = yield self.make_client()
        yield c1.next_non_ack()
        c1.send(u"bind", appid=u"appid", side=u"side")
        app = self._rendezvous.get_app(u"appid")

        c1.send(u"close", mood=u"mood") # must open first
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"must open mailbox before closing")

        c1.send(u"open", mailbox=u"mb1")
        c1.send(u"close", mood=u"mood")
        m = yield c1.next_non_ack()
        self.assertEqual(m[u"type"], u"closed")

        c1.send(u"close", mood=u"mood") # already closed
        err = yield c1.next_non_ack()
        self.assertEqual(err[u"type"], u"error")
        self.assertEqual(err[u"error"], u"must open mailbox before closing")


class Summary(unittest.TestCase):
    def test_mailbox(self):
        c = rendezvous.Mailbox(None, None, None, False, None, None)
        # starts at time 1, maybe gets second open at time 3, closes at 5
        base_row = {u"started": 1, u"second": None,
                    u"first_mood": None, u"crowded": False}
        def summ(num_sides, second_mood=None, pruned=False, **kwargs):
            row = base_row.copy()
            row.update(kwargs)
            return c._summarize(row, num_sides, second_mood, 5, pruned)

        self.assertEqual(summ(1), Usage(1, None, 4, u"lonely"))
        self.assertEqual(summ(1, u"lonely"), Usage(1, None, 4, u"lonely"))
        self.assertEqual(summ(1, u"errory"), Usage(1, None, 4, u"errory"))
        self.assertEqual(summ(1, crowded=True), Usage(1, None, 4, u"crowded"))

        self.assertEqual(summ(2, first_mood=u"happy",
                              second=3, second_mood=u"happy"),
                         Usage(1, 2, 4, u"happy"))

        self.assertEqual(summ(2, first_mood=u"errory",
                              second=3, second_mood=u"happy"),
                         Usage(1, 2, 4, u"errory"))

        self.assertEqual(summ(2, first_mood=u"happy",
                              second=3, second_mood=u"errory"),
                         Usage(1, 2, 4, u"errory"))

        self.assertEqual(summ(2, first_mood=u"scary",
                              second=3, second_mood=u"happy"),
                         Usage(1, 2, 4, u"scary"))

        self.assertEqual(summ(2, first_mood=u"scary",
                              second=3, second_mood=u"errory"),
                         Usage(1, 2, 4, u"scary"))

        self.assertEqual(summ(2, first_mood=u"happy", second=3, pruned=True),
                         Usage(1, 2, 4, u"pruney"))

    def test_nameplate(self):
        a = rendezvous.AppNamespace(None, None, None, False, None)
        # starts at time 1, maybe gets second open at time 3, closes at 5
        base_row = {u"started": 1, u"second": None, u"crowded": False}
        def summ(num_sides, pruned=False, **kwargs):
            row = base_row.copy()
            row.update(kwargs)
            return a._summarize_nameplate_usage(row, 5, pruned)

        self.assertEqual(summ(1), Usage(1, None, 4, u"lonely"))
        self.assertEqual(summ(1, crowded=True), Usage(1, None, 4, u"crowded"))

        self.assertEqual(summ(2, second=3), Usage(1, 2, 4, u"happy"))

        self.assertEqual(summ(2, second=3, pruned=True),
                         Usage(1, 2, 4, u"pruney"))

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
