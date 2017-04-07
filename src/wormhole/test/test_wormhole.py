from __future__ import print_function, unicode_literals
import json, io, re
import mock
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import gatherResults, inlineCallbacks
from .common import ServerBase, poll_until, pause_one_tick
from .. import wormhole, _rendezvous
from ..errors import (WrongPasswordError,
                      KeyFormatError, WormholeClosed, LonelyError,
                      NoKeyError, OnlyOneCodeError)

APPID = "appid"

class MockWebSocket:
    def __init__(self):
        self._payloads = []
    def sendMessage(self, payload, is_binary):
        assert not is_binary
        self._payloads.append(payload)

    def outbound(self):
        out = []
        while self._payloads:
            p = self._payloads.pop(0)
            out.append(json.loads(p.decode("utf-8")))
        return out

def response(w, **kwargs):
    payload = json.dumps(kwargs).encode("utf-8")
    w._ws_dispatch_response(payload)

class Welcome(unittest.TestCase):
    def test_tolerate_no_current_version(self):
        w = wormhole._WelcomeHandler("relay_url")
        w.handle_welcome({})

    def test_print_motd(self):
        stderr = io.StringIO()
        w = wormhole._WelcomeHandler("relay_url", stderr=stderr)
        w.handle_welcome({"motd": "message of\nthe day"})
        self.assertEqual(stderr.getvalue(),
                         "Server (at relay_url) says:\n message of\n the day\n")
        # motd can be displayed multiple times
        w.handle_welcome({"motd": "second message"})
        self.assertEqual(stderr.getvalue(),
                         ("Server (at relay_url) says:\n message of\n the day\n"
                          "Server (at relay_url) says:\n second message\n"))

# event orderings to exercise:
#
# * normal sender: set_code, send_phase1, connected, claimed, learn_msg2,
#   learn_phase1
# * normal receiver (argv[2]=code): set_code, connected, learn_msg1,
#   learn_phase1, send_phase1,
# * normal receiver (readline): connected, input_code
# *
# * set_code, then connected
# * connected, receive_pake, send_phase, set_code

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

class Delegated(ServerBase, unittest.TestCase):

    def test_delegated(self):
        dg = Delegate()
        w = wormhole.create(APPID, self.relayurl, reactor, delegate=dg)
        w.close()

class Wormholes(ServerBase, unittest.TestCase):
    # integration test, with a real server

    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_allocate_default(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        mo = re.search(r"^\d+-\w+-\w+$", code)
        self.assert_(mo, code)
        # w.close() fails because we closed before connecting
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_allocate_more_words(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code(3)
        code = yield w1.when_code()
        mo = re.search(r"^\d+-\w+-\w+-\w+$", code)
        self.assert_(mo, code)
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        #w1.debug_set_trace("W1")
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        #w2.debug_set_trace("  W2")
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)

        yield w1.when_key()
        yield w2.when_key()

        verifier1 = yield w1.when_verified()
        verifier2 = yield w2.when_verified()
        self.assertEqual(verifier1, verifier2)

        self.successResultOf(w1.when_key())
        self.successResultOf(w2.when_key())

        version1 = yield w1.when_version()
        version2 = yield w2.when_version()
        # app-versions are exercised properly in test_versions, this just
        # tests the defaults
        self.assertEqual(version1, {})
        self.assertEqual(version2, {})

        w1.send(b"data1")
        w2.send(b"data2")
        dataX = yield w1.when_received()
        dataY = yield w2.when_received()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")

        version1_again = yield w1.when_version()
        self.assertEqual(version1, version1_again)

        c1 = yield w1.close()
        self.assertEqual(c1, "happy")
        c2 = yield w2.close()
        self.assertEqual(c2, "happy")

    @inlineCallbacks
    def test_when_code_early(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        d = w1.when_code()
        w1.set_code("1-abc")
        code = self.successResultOf(d)
        self.assertEqual(code, "1-abc")
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_when_code_late(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("1-abc")
        d = w1.when_code()
        code = self.successResultOf(d)
        self.assertEqual(code, "1-abc")
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)
        w1.send(b"data")
        w2.send(b"data")
        dataX = yield w1.when_received()
        dataY = yield w2.when_received()
        self.assertEqual(dataX, b"data")
        self.assertEqual(dataY, b"data")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_interleaved(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)
        w1.send(b"data1")
        dataY = yield w2.when_received()
        self.assertEqual(dataY, b"data1")
        d = w1.when_received()
        w2.send(b"data2")
        dataX = yield d
        self.assertEqual(dataX, b"data2")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_unidirectional(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)
        w1.send(b"data1")
        dataY = yield w2.when_received()
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_early(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.send(b"data1")
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        d = w2.when_received()
        w1.set_code("123-abc-def")
        w2.set_code("123-abc-def")
        dataY = yield d
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_fixed_code(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        dl = yield self.doBoth(w1.when_received(), w2.when_received())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_input_code(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        h = w2.input_code()
        h.choose_nameplate("123")
        # Pause to allow some messages to get delivered. Specifically we want
        # to wait until w2 claims the nameplate, opens the mailbox, and
        # receives the PAKE message, to exercise the PAKE-before-CODE path in
        # Key.
        yield poll_until(lambda: w2._boss._K._debug_pake_stashed)
        h.choose_words("purple-elephant")

        w1.send(b"data1"), w2.send(b"data2")
        dl = yield self.doBoth(w1.when_received(), w2.when_received())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()


    @inlineCallbacks
    def test_multiple_messages(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        w1.send(b"data3"), w2.send(b"data4")
        dl = yield self.doBoth(w1.when_received(), w2.when_received())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.when_received(), w2.when_received())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data4")
        self.assertEqual(dataY, b"data3")
        yield w1.close()
        yield w2.close()


    @inlineCallbacks
    def test_closed(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("123-foo")
        w2.set_code("123-foo")

        # let it connect and become HAPPY
        yield w1.when_version()
        yield w2.when_version()

        yield w1.close()
        yield w2.close()

        # once closed, all Deferred-yielding API calls get an immediate error
        f = self.failureResultOf(w1.when_code(), WormholeClosed)
        self.assertEqual(f.value.args[0], "happy")
        self.failureResultOf(w1.when_key(), WormholeClosed)
        self.failureResultOf(w1.when_verified(), WormholeClosed)
        self.failureResultOf(w1.when_version(), WormholeClosed)
        self.failureResultOf(w1.when_received(), WormholeClosed)


    @inlineCallbacks
    def test_wrong_password(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code+"not")
        code2 = yield w2.when_code()
        self.assertNotEqual(code, code2)
        # That's enough to allow both sides to discover the mismatch, but
        # only after the confirmation message gets through. API calls that
        # don't wait will appear to work until the mismatched confirmation
        # message arrives.
        w1.send(b"should still work")
        w2.send(b"should still work")

        key2 = yield w2.when_key() # should work
        # w2 has just received w1.PAKE, and is about to send w2.VERSION
        key1 = yield w1.when_key() # should work
        # w1 has just received w2.PAKE, and is about to send w1.VERSION, and
        # then will receive w2.VERSION. When it sees w2.VERSION, it will
        # learn about the WrongPasswordError.
        self.assertNotEqual(key1, key2)

        # API calls that wait (i.e. get) will errback. We collect all these
        # Deferreds early to exercise the wait-then-fail path
        d1_verified = w1.when_verified()
        d1_version = w1.when_version()
        d1_received = w1.when_received()
        d2_verified = w2.when_verified()
        d2_version = w2.when_version()
        d2_received = w2.when_received()

        # wait for each side to notice the failure
        yield self.assertFailure(w1.when_verified(), WrongPasswordError)
        yield self.assertFailure(w2.when_verified(), WrongPasswordError)
        # and then wait for the rest of the loops to fire. if we had+used
        # eventual-send, this wouldn't be a problem
        yield pause_one_tick()

        # now all the rest should have fired already
        self.failureResultOf(d1_verified, WrongPasswordError)
        self.failureResultOf(d1_version, WrongPasswordError)
        self.failureResultOf(d1_received, WrongPasswordError)
        self.failureResultOf(d2_verified, WrongPasswordError)
        self.failureResultOf(d2_version, WrongPasswordError)
        self.failureResultOf(d2_received, WrongPasswordError)

        # and at this point, with the failure safely noticed by both sides,
        # new when_key() calls should signal the failure, even before we
        # close

        # any new calls in the error state should immediately fail
        self.failureResultOf(w1.when_key(), WrongPasswordError)
        self.failureResultOf(w1.when_verified(), WrongPasswordError)
        self.failureResultOf(w1.when_version(), WrongPasswordError)
        self.failureResultOf(w1.when_received(), WrongPasswordError)
        self.failureResultOf(w2.when_key(), WrongPasswordError)
        self.failureResultOf(w2.when_verified(), WrongPasswordError)
        self.failureResultOf(w2.when_version(), WrongPasswordError)
        self.failureResultOf(w2.when_received(), WrongPasswordError)

        yield self.assertFailure(w1.close(), WrongPasswordError)
        yield self.assertFailure(w2.close(), WrongPasswordError)

        # API calls should still get the error, not WormholeClosed
        self.failureResultOf(w1.when_key(), WrongPasswordError)
        self.failureResultOf(w1.when_verified(), WrongPasswordError)
        self.failureResultOf(w1.when_version(), WrongPasswordError)
        self.failureResultOf(w1.when_received(), WrongPasswordError)
        self.failureResultOf(w2.when_key(), WrongPasswordError)
        self.failureResultOf(w2.when_verified(), WrongPasswordError)
        self.failureResultOf(w2.when_version(), WrongPasswordError)
        self.failureResultOf(w2.when_received(), WrongPasswordError)

    @inlineCallbacks
    def test_wrong_password_with_spaces(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        badcode = "4 oops spaces"
        with self.assertRaises(KeyFormatError) as ex:
            w.set_code(badcode)
        expected_msg = "code (%s) contains spaces." % (badcode,)
        self.assertEqual(expected_msg, str(ex.exception))
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_verifier(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)
        v1 = yield w1.when_verified() # early
        v2 = yield w2.when_verified()
        self.failUnlessEqual(type(v1), type(b""))
        self.failUnlessEqual(v1, v2)
        w1.send(b"data1")
        w2.send(b"data2")
        dataX = yield w1.when_received()
        dataY = yield w2.when_received()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")

        # calling when_verified() this late should fire right away
        v1_late = self.successResultOf(w2.when_verified())
        self.assertEqual(v1_late, v1)

        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_versions(self):
        # there's no API for this yet, but make sure the internals work
        w1 = wormhole.create(APPID, self.relayurl, reactor,
                             versions={"w1": 123})
        w2 = wormhole.create(APPID, self.relayurl, reactor,
                             versions={"w2": 456})
        w1.allocate_code()
        code = yield w1.when_code()
        w2.set_code(code)
        w1_versions = yield w2.when_version()
        self.assertEqual(w1_versions, {"w1": 123})
        w2_versions = yield w1.when_version()
        self.assertEqual(w2_versions, {"w2": 456})
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_rx_dedup(self):
        # Future clients will handle losing/reestablishing the Rendezvous
        # Server connection by retransmitting messages, which will sometimes
        # cause duplicate messages. Make sure this client can tolerate them.
        # The first place this would fail was when the second copy of the
        # incoming PAKE message was received, which would cause
        # SPAKE2.finish() to be called a second time, which throws an error
        # (which, being somewhat unexpected, caused a hang rather than a
        # clear exception). The Mailbox object is responsible for
        # deduplication, so we must patch the RendezvousConnector to simulate
        # duplicated messages.
        with mock.patch("wormhole._boss.RendezvousConnector", MessageDoubler):
            w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        dl = yield self.doBoth(w1.when_received(), w2.when_received())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

class MessageDoubler(_rendezvous.RendezvousConnector):
    # we could double messages on the sending side, but a future server will
    # strip those duplicates, so to really exercise the receiver, we must
    # double them on the inbound side instead
    #def _msg_send(self, phase, body):
    #    wormhole._Wormhole._msg_send(self, phase, body)
    #    self._ws_send_command("add", phase=phase, body=bytes_to_hexstr(body))
    def _response_handle_message(self, msg):
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)

class Errors(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_derive_key_early(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        # definitely too early
        self.assertRaises(NoKeyError, w.derive_key, "purpose", 12)
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_multiple_set_code(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        w.set_code("123-purple-elephant")
        # code can only be set once
        self.assertRaises(OnlyOneCodeError, w.set_code, "123-nope")
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_allocate_and_set_code(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        w.allocate_code()
        yield w.when_code()
        self.assertRaises(OnlyOneCodeError, w.set_code, "123-nope")
        yield self.assertFailure(w.close(), LonelyError)
