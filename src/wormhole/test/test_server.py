from __future__ import print_function
import sys, json
import requests
from six.moves.urllib_parse import urlencode
from twisted.trial import unittest
from twisted.internet import reactor, defer
from twisted.internet.threads import deferToThread
from twisted.web.client import getPage, Agent, readBody
from .. import __version__
from .common import ServerBase
from ..twisted.eventsource_twisted import EventSource

class Reachable(ServerBase, unittest.TestCase):

    def test_getPage(self):
        # client.getPage requires str/unicode URL, returns bytes
        url = self.relayurl.replace("wormhole-relay/", "").encode("ascii")
        d = getPage(url)
        def _got(res):
            self.failUnlessEqual(res, b"Wormhole Relay\n")
        d.addCallback(_got)
        return d

    def test_agent(self):
        # client.Agent is not yet ported: it wants URLs to be both unicode
        # and bytes at the same time.
        # https://twistedmatrix.com/trac/ticket/7407
        if sys.version_info[0] > 2:
            raise unittest.SkipTest("twisted.web.client.Agent does not yet support py3")
        url = self.relayurl.replace("wormhole-relay/", "").encode("ascii")
        agent = Agent(reactor)
        d = agent.request("GET", url)
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

    def test_messages(self):
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

    def test_eventsource(self):
        if sys.version_info[0] >= 3:
            raise unittest.SkipTest("twisted vs py3")

        d = self.post("allocate", {"appid": "app1", "side": "abc"})
        def _allocated(data):
            self.cid = data["channelid"]
            url = self.build_url("get", "app1", self.cid)
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
        self.connected_d.callback(None)

    def wait_for_disconnection(self):
        return self.disconnected_d
    def disconnected(self, why):
        self.disconnected_d.callback((why,))

