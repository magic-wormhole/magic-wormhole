import sys
import requests
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.threads import deferToThread
from twisted.web.client import getPage, Agent, readBody
from .common import ServerBase

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
