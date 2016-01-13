from __future__ import print_function
import json
import requests
from six.moves.urllib_parse import urlencode
from twisted.trial import unittest
from twisted.internet import reactor, defer
from twisted.internet.threads import deferToThread
from twisted.web.client import getPage, Agent, readBody
from .. import __version__
from .common import ServerBase
from ..servers import relay_server, transit_server
from ..twisted.eventsource_twisted import EventSource

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

class API(ServerBase, unittest.TestCase):
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
            self.failUnlessEqual(data["messages"],
                                 [{"phase": "1", "body": "msg1A"}])
        d.addCallback(_check1)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check1)
        d.addCallback(lambda _: self.add_message("msg1B", side="def"))
        def _check2(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(data["messages"]),
                                 set([("1", "msg1A"),
                                      ("1", "msg1B")]))
        d.addCallback(_check2)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check2)

        # adding a duplicate message is not an error, is ignored by clients
        d.addCallback(lambda _: self.add_message("msg1B", side="def"))
        def _check3(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(data["messages"]),
                                 set([("1", "msg1A"),
                                      ("1", "msg1B")]))
        d.addCallback(_check3)
        d.addCallback(lambda _: self.get("get", "app1", str(self.cid)))
        d.addCallback(_check3)

        d.addCallback(lambda _: self.add_message("msg2A", side="abc",
                                                 phase="2"))
        def _check4(data):
            self.check_welcome(data)
            self.failUnlessEqual(self.parse_messages(data["messages"]),
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
            self.failUnlessEqual(data, {"phase": "1", "body": "msg1A"})
        d.addCallback(_check_msg1)

        d.addCallback(lambda _: self.add_message("msg1B"))
        d.addCallback(lambda _: self.add_message("msg2A", phase="2"))
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_msg2(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "message")
            self.failUnlessEqual(data, {"phase": "1", "body": "msg1B"})
        d.addCallback(_check_msg2)
        d.addCallback(lambda _: self.o.wait_for_next_event())
        def _check_msg3(ev):
            eventtype, data = ev
            self.failUnlessEqual(eventtype, "message")
            self.failUnlessEqual(data, {"phase": "2", "body": "msg2A"})
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

class Summary(unittest.TestCase):
    def test_summarize(self):
        c = relay_server.Channel(None, None, None, None, False, None, None)
        A = relay_server.ALLOCATE
        D = relay_server.DEALLOCATE

        messages = [{"when": 1, "side": "a", "phase": A}]
        self.failUnlessEqual(c._summarize(messages, 2),
                             (1, "lonely", 1, None))

        messages = [{"when": 1, "side": "a", "phase": A},
                    {"when": 2, "side": "a", "phase": D, "body": "lonely"},
                    ]
        self.failUnlessEqual(c._summarize(messages, 3),
                             (1, "lonely", 2, None))

        messages = [{"when": 1, "side": "a", "phase": A},
                    {"when": 2, "side": "b", "phase": A},
                    {"when": 3, "side": "c", "phase": A},
                    ]
        self.failUnlessEqual(c._summarize(messages, 4),
                             (1, "crowded", 3, None))

        base = [{"when": 1, "side": "a", "phase": A},
                {"when": 2, "side": "a", "phase": "pake", "body": "msg1"},
                {"when": 10, "side": "b", "phase": "pake", "body": "msg2"},
                {"when": 11, "side": "b", "phase": "data", "body": "msg3"},
                {"when": 20, "side": "a", "phase": "data", "body": "msg4"},
                ]
        def make_moods(A_mood, B_mood):
            return base + [
                {"when": 21, "side": "a", "phase": D, "body": A_mood},
                {"when": 30, "side": "b", "phase": D, "body": B_mood},
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

class Transit(unittest.TestCase):
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

