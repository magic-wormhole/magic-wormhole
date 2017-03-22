from __future__ import print_function, unicode_literals
import re
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from .common import ServerBase
from .. import wormhole, errors

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
    timeout = 2

    @inlineCallbacks
    def test_allocate(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        #w.debug_set_trace("W1")
        w.allocate_code(2)
        code = yield w.when_code()
        self.assertEqual(type(code), type(""))
        mo = re.search(r"^\d+-\w+-\w+$", code)
        self.assert_(mo, code)
        # w.close() fails because we closed before connecting
        yield self.assertFailure(w.close(), errors.LonelyError)

    def test_delegated(self):
        dg = Delegate()
        w = wormhole.create(APPID, self.relayurl, reactor, delegate=dg)
        w.close()

    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        #w1.debug_set_trace("W1")
        w1.allocate_code(2)
        code = yield w1.when_code()
        mo = re.search(r"^\d+-\w+-\w+$", code)
        self.assert_(mo, code)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        #w2.debug_set_trace("  W2")
        w2.set_code(code)
        code2 = yield w2.when_code()
        self.assertEqual(code, code2)

        verifier1 = yield w1.when_verified()
        verifier2 = yield w2.when_verified()
        self.assertEqual(verifier1, verifier2)

        version1 = yield w1.when_version()
        version2 = yield w2.when_version()
        # TODO: add the ability to set app-versions
        self.assertEqual(version1, {})
        self.assertEqual(version2, {})

        w1.send(b"data")

        data = yield w2.when_received()
        self.assertEqual(data, b"data")

        w2.send(b"data2")
        data2 = yield w1.when_received()
        self.assertEqual(data2, b"data2")

        c1 = yield w1.close()
        self.assertEqual(c1, "happy")
        c2 = yield w2.close()
        self.assertEqual(c2, "happy")

    @inlineCallbacks
    def test_wrong_password(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        #w1.debug_set_trace("W1")
        w1.allocate_code(2)
        code = yield w1.when_code()
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w2.set_code(code+"NOT")
        code2 = yield w2.when_code()
        self.assertNotEqual(code, code2)

        w1.send(b"data")

        yield self.assertFailure(w2.when_received(), errors.WrongPasswordError)
        # wait for w1.when_received, because if we close w1 before it has
        # seen the VERSION message, we could legitimately get LonelyError
        # instead of WrongPasswordError. w2 didn't send anything, so
        # w1.when_received wouldn't ever callback, but it will errback when
        # w1 gets the undecryptable VERSION.
        yield self.assertFailure(w1.when_received(), errors.WrongPasswordError)
        yield self.assertFailure(w1.close(), errors.WrongPasswordError)
        yield self.assertFailure(w2.close(), errors.WrongPasswordError)
