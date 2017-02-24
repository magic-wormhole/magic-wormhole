from __future__ import print_function, unicode_literals
from twisted.trial import unittest
from twisted.internet import reactor
from .common import ServerBase
from .. import wormhole

APPID = "appid"

class Delegate:
    def __init__(self):
        self.code = None
        self.verifier = None
        self.messages = []
        self.closed = None
    def wormhole_got_code(self, code):
        self.code = code
    def wormhole_got_verifier(self, verifier):
        self.verifier = verifier
    def wormhole_receive(self, data):
        self.messages.append(data)
    def wormhole_closed(self, result):
        self.closed = result

class New(ServerBase, unittest.TestCase):
    def test_basic(self):
        dg = Delegate()
        w = wormhole.delegated_wormhole(APPID, self.relayurl, reactor, dg)
        w.close()
