from __future__ import print_function, unicode_literals

import io
import re

from twisted.internet import reactor
from twisted.internet.defer import gatherResults, inlineCallbacks, returnValue
from twisted.internet.error import ConnectionRefusedError
from twisted.trial import unittest

import mock

from .. import _rendezvous, wormhole
from ..errors import (KeyFormatError, LonelyError, NoKeyError,
                      OnlyOneCodeError, ServerConnectionError, WormholeClosed,
                      WrongPasswordError)
from ..eventual import EventualQueue
from ..transit import allocate_tcp_port
from .common import ServerBase, poll_until

APPID = "appid"

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
        self.welcome = None
        self.code = None
        self.key = None
        self.verifier = None
        self.versions = None
        self.messages = []
        self.closed = None

    def wormhole_got_welcome(self, welcome):
        self.welcome = welcome

    def wormhole_got_code(self, code):
        self.code = code

    def wormhole_got_unverified_key(self, key):
        self.key = key

    def wormhole_got_verifier(self, verifier):
        self.verifier = verifier

    def wormhole_got_versions(self, versions):
        self.versions = versions

    def wormhole_got_message(self, data):
        self.messages.append(data)

    def wormhole_closed(self, result):
        self.closed = result


class Delegated(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_delegated(self):
        dg = Delegate()
        w1 = wormhole.create(APPID, self.relayurl, reactor, delegate=dg)
        # w1.debug_set_trace("W1")
        with self.assertRaises(NoKeyError):
            w1.derive_key("purpose", 12)
        w1.set_code("1-abc")
        self.assertEqual(dg.code, "1-abc")
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w2.set_code(dg.code)
        yield poll_until(lambda: dg.key is not None)
        yield poll_until(lambda: dg.verifier is not None)
        yield poll_until(lambda: dg.versions is not None)

        w1.send_message(b"ping")
        got = yield w2.get_message()
        self.assertEqual(got, b"ping")
        w2.send_message(b"pong")
        yield poll_until(lambda: dg.messages)
        self.assertEqual(dg.messages[0], b"pong")

        key1 = w1.derive_key("purpose", 16)
        self.assertEqual(len(key1), 16)
        self.assertEqual(type(key1), type(b""))
        with self.assertRaises(TypeError):
            w1.derive_key(b"not unicode", 16)
        with self.assertRaises(TypeError):
            w1.derive_key(12345, 16)

        w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_allocate_code(self):
        dg = Delegate()
        w1 = wormhole.create(APPID, self.relayurl, reactor, delegate=dg)
        w1.allocate_code()
        yield poll_until(lambda: dg.code is not None)
        w1.close()

    @inlineCallbacks
    def test_input_code(self):
        dg = Delegate()
        w1 = wormhole.create(APPID, self.relayurl, reactor, delegate=dg)
        h = w1.input_code()
        h.choose_nameplate("123")
        h.choose_words("purple-elephant")
        yield poll_until(lambda: dg.code is not None)
        w1.close()


class Wormholes(ServerBase, unittest.TestCase):
    # integration test, with a real server

    def setUp(self):
        # test_welcome wants to see [current_cli_version]
        self._setup_relay(None, advertise_version="advertised.version")

    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_allocate_default(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.get_code()
        mo = re.search(r"^\d+-\w+-\w+$", code)
        self.assert_(mo, code)
        # w.close() fails because we closed before connecting
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_allocate_more_words(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code(3)
        code = yield w1.get_code()
        mo = re.search(r"^\d+-\w+-\w+-\w+$", code)
        self.assert_(mo, code)
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        # w1.debug_set_trace("W1")
        with self.assertRaises(NoKeyError):
            w1.derive_key("purpose", 12)

        w2 = wormhole.create(APPID, self.relayurl, reactor)
        # w2.debug_set_trace("  W2")
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)

        yield w1.get_unverified_key()
        yield w2.get_unverified_key()

        key1 = w1.derive_key("purpose", 16)
        self.assertEqual(len(key1), 16)
        self.assertEqual(type(key1), type(b""))
        with self.assertRaises(TypeError):
            w1.derive_key(b"not unicode", 16)
        with self.assertRaises(TypeError):
            w1.derive_key(12345, 16)

        verifier1 = yield w1.get_verifier()
        verifier2 = yield w2.get_verifier()
        self.assertEqual(verifier1, verifier2)

        versions1 = yield w1.get_versions()
        versions2 = yield w2.get_versions()
        # app-versions are exercised properly in test_versions, this just
        # tests the defaults
        self.assertEqual(versions1, {})
        self.assertEqual(versions2, {})

        w1.send_message(b"data1")
        w2.send_message(b"data2")
        dataX = yield w1.get_message()
        dataY = yield w2.get_message()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")

        versions1_again = yield w1.get_versions()
        self.assertEqual(versions1, versions1_again)

        c1 = yield w1.close()
        self.assertEqual(c1, "happy")
        c2 = yield w2.close()
        self.assertEqual(c2, "happy")

    @inlineCallbacks
    def test_get_code_early(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        d = w1.get_code()
        w1.set_code("1-abc")
        yield eq.flush()
        code = self.successResultOf(d)
        self.assertEqual(code, "1-abc")
        yield self.assertFailure(w1.close(), LonelyError)

    @inlineCallbacks
    def test_get_code_late(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w1.set_code("1-abc")
        d = w1.get_code()
        yield eq.flush()
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
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send_message(b"data")
        w2.send_message(b"data")
        dataX = yield w1.get_message()
        dataY = yield w2.get_message()
        self.assertEqual(dataX, b"data")
        self.assertEqual(dataY, b"data")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_interleaved(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send_message(b"data1")
        dataY = yield w2.get_message()
        self.assertEqual(dataY, b"data1")
        d = w1.get_message()
        w2.send_message(b"data2")
        dataX = yield d
        self.assertEqual(dataX, b"data2")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_unidirectional(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send_message(b"data1")
        dataY = yield w2.get_message()
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_early(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1.send_message(b"data1")
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        d = w2.get_message()
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
        w1.send_message(b"data1"), w2.send_message(b"data2")
        dl = yield self.doBoth(w1.get_message(), w2.get_message())
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

        w1.send_message(b"data1"), w2.send_message(b"data2")
        dl = yield self.doBoth(w1.get_message(), w2.get_message())
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
        w1.send_message(b"data1"), w2.send_message(b"data2")
        w1.send_message(b"data3"), w2.send_message(b"data4")
        dl = yield self.doBoth(w1.get_message(), w2.get_message())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get_message(), w2.get_message())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data4")
        self.assertEqual(dataY, b"data3")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_closed(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w1.set_code("123-foo")
        w2.set_code("123-foo")

        # let it connect and become HAPPY
        yield w1.get_versions()
        yield w2.get_versions()

        yield w1.close()
        yield w2.close()

        # once closed, all Deferred-yielding API calls get an prompt error
        yield self.assertFailure(w1.get_welcome(), WormholeClosed)
        e = yield self.assertFailure(w1.get_code(), WormholeClosed)
        self.assertEqual(e.args[0], "happy")
        yield self.assertFailure(w1.get_unverified_key(), WormholeClosed)
        yield self.assertFailure(w1.get_verifier(), WormholeClosed)
        yield self.assertFailure(w1.get_versions(), WormholeClosed)
        yield self.assertFailure(w1.get_message(), WormholeClosed)

    @inlineCallbacks
    def test_closed_idle(self):
        yield self._relay_server.disownServiceParent()
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        # without a relay server, this won't ever connect

        d_welcome = w1.get_welcome()
        self.assertNoResult(d_welcome)
        d_code = w1.get_code()
        d_key = w1.get_unverified_key()
        d_verifier = w1.get_verifier()
        d_versions = w1.get_versions()
        d_message = w1.get_message()

        yield self.assertFailure(w1.close(), LonelyError)

        yield self.assertFailure(d_welcome, LonelyError)
        yield self.assertFailure(d_code, LonelyError)
        yield self.assertFailure(d_key, LonelyError)
        yield self.assertFailure(d_verifier, LonelyError)
        yield self.assertFailure(d_versions, LonelyError)
        yield self.assertFailure(d_message, LonelyError)

    @inlineCallbacks
    def test_wrong_password(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code + "not")
        code2 = yield w2.get_code()
        self.assertNotEqual(code, code2)
        # That's enough to allow both sides to discover the mismatch, but
        # only after the confirmation message gets through. API calls that
        # don't wait will appear to work until the mismatched confirmation
        # message arrives.
        w1.send_message(b"should still work")
        w2.send_message(b"should still work")

        key2 = yield w2.get_unverified_key()  # should work
        # w2 has just received w1.PAKE, and is about to send w2.VERSION
        key1 = yield w1.get_unverified_key()  # should work
        # w1 has just received w2.PAKE, and is about to send w1.VERSION, and
        # then will receive w2.VERSION. When it sees w2.VERSION, it will
        # learn about the WrongPasswordError.
        self.assertNotEqual(key1, key2)

        # API calls that wait (i.e. get) will errback. We collect all these
        # Deferreds early to exercise the wait-then-fail path
        d1_verified = w1.get_verifier()
        d1_versions = w1.get_versions()
        d1_received = w1.get_message()
        d2_verified = w2.get_verifier()
        d2_versions = w2.get_versions()
        d2_received = w2.get_message()

        # wait for each side to notice the failure
        yield self.assertFailure(w1.get_verifier(), WrongPasswordError)
        yield self.assertFailure(w2.get_verifier(), WrongPasswordError)
        # the rest of the loops should fire within the next tick
        yield eq.flush()

        # now all the rest should have fired already
        self.failureResultOf(d1_verified, WrongPasswordError)
        self.failureResultOf(d1_versions, WrongPasswordError)
        self.failureResultOf(d1_received, WrongPasswordError)
        self.failureResultOf(d2_verified, WrongPasswordError)
        self.failureResultOf(d2_versions, WrongPasswordError)
        self.failureResultOf(d2_received, WrongPasswordError)

        # and at this point, with the failure safely noticed by both sides,
        # new get_unverified_key() calls should signal the failure, even
        # before we close

        # any new calls in the error state should immediately fail
        yield self.assertFailure(w1.get_unverified_key(), WrongPasswordError)
        yield self.assertFailure(w1.get_verifier(), WrongPasswordError)
        yield self.assertFailure(w1.get_versions(), WrongPasswordError)
        yield self.assertFailure(w1.get_message(), WrongPasswordError)
        yield self.assertFailure(w2.get_unverified_key(), WrongPasswordError)
        yield self.assertFailure(w2.get_verifier(), WrongPasswordError)
        yield self.assertFailure(w2.get_versions(), WrongPasswordError)
        yield self.assertFailure(w2.get_message(), WrongPasswordError)

        yield self.assertFailure(w1.close(), WrongPasswordError)
        yield self.assertFailure(w2.close(), WrongPasswordError)

        # API calls should still get the error, not WormholeClosed
        yield self.assertFailure(w1.get_unverified_key(), WrongPasswordError)
        yield self.assertFailure(w1.get_verifier(), WrongPasswordError)
        yield self.assertFailure(w1.get_versions(), WrongPasswordError)
        yield self.assertFailure(w1.get_message(), WrongPasswordError)
        yield self.assertFailure(w2.get_unverified_key(), WrongPasswordError)
        yield self.assertFailure(w2.get_verifier(), WrongPasswordError)
        yield self.assertFailure(w2.get_versions(), WrongPasswordError)
        yield self.assertFailure(w2.get_message(), WrongPasswordError)

    @inlineCallbacks
    def test_wrong_password_with_spaces(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        badcode = "4 oops spaces"
        with self.assertRaises(KeyFormatError) as ex:
            w.set_code(badcode)
        expected_msg = "Code '%s' contains spaces." % (badcode, )
        self.assertEqual(expected_msg, str(ex.exception))
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_wrong_password_with_leading_space(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        badcode = " 4-oops-space"
        with self.assertRaises(KeyFormatError) as ex:
            w.set_code(badcode)
        expected_msg = "Code '%s' contains spaces." % (badcode, )
        self.assertEqual(expected_msg, str(ex.exception))
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_wrong_password_with_non_numeric_nameplate(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        badcode = "four-oops-space"
        with self.assertRaises(KeyFormatError) as ex:
            w.set_code(badcode)
        expected_msg = "Nameplate 'four' must be numeric, with no spaces."
        self.assertEqual(expected_msg, str(ex.exception))
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_welcome(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        wel1 = yield w1.get_welcome()  # early: before connection established
        wel2 = yield w1.get_welcome()  # late: already received welcome
        self.assertEqual(wel1, wel2)
        self.assertIn("current_cli_version", wel1)

        # cause an error, so a later get_welcome will return the error
        w1.set_code("123-foo")
        w2 = wormhole.create(APPID, self.relayurl, reactor)
        w2.set_code("123-NOT")
        yield self.assertFailure(w1.get_verifier(), WrongPasswordError)

        yield self.assertFailure(w1.get_welcome(), WrongPasswordError)  # late

        yield self.assertFailure(w1.close(), WrongPasswordError)
        yield self.assertFailure(w2.close(), WrongPasswordError)

    @inlineCallbacks
    def test_verifier(self):
        eq = EventualQueue(reactor)
        w1 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w2 = wormhole.create(APPID, self.relayurl, reactor, _eventual_queue=eq)
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        v1 = yield w1.get_verifier()  # early
        v2 = yield w2.get_verifier()
        self.failUnlessEqual(type(v1), type(b""))
        self.failUnlessEqual(v1, v2)
        w1.send_message(b"data1")
        w2.send_message(b"data2")
        dataX = yield w1.get_message()
        dataY = yield w2.get_message()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")

        # calling get_verifier() this late should fire right away
        d = w2.get_verifier()
        yield eq.flush()
        v1_late = self.successResultOf(d)
        self.assertEqual(v1_late, v1)

        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_versions(self):
        # there's no API for this yet, but make sure the internals work
        w1 = wormhole.create(
            APPID, self.relayurl, reactor, versions={"w1": 123})
        w2 = wormhole.create(
            APPID, self.relayurl, reactor, versions={"w2": 456})
        w1.allocate_code()
        code = yield w1.get_code()
        w2.set_code(code)
        w1_versions = yield w2.get_versions()
        self.assertEqual(w1_versions, {"w1": 123})
        w2_versions = yield w1.get_versions()
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
        w1.send_message(b"data1"), w2.send_message(b"data2")
        dl = yield self.doBoth(w1.get_message(), w2.get_message())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()


class MessageDoubler(_rendezvous.RendezvousConnector):
    # we could double messages on the sending side, but a future server will
    # strip those duplicates, so to really exercise the receiver, we must
    # double them on the inbound side instead
    # def _msg_send(self, phase, body):
    #     wormhole._Wormhole._msg_send(self, phase, body)
    #     self._ws_send_command("add", phase=phase, body=bytes_to_hexstr(body))
    def _response_handle_message(self, msg):
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)


class Errors(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_derive_key_early(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        # definitely too early
        with self.assertRaises(NoKeyError):
            w.derive_key("purpose", 12)
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_multiple_set_code(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        w.set_code("123-purple-elephant")
        # code can only be set once
        with self.assertRaises(OnlyOneCodeError):
            w.set_code("123-nope")
        yield self.assertFailure(w.close(), LonelyError)

    @inlineCallbacks
    def test_allocate_and_set_code(self):
        w = wormhole.create(APPID, self.relayurl, reactor)
        w.allocate_code()
        yield w.get_code()
        with self.assertRaises(OnlyOneCodeError):
            w.set_code("123-nope")
        yield self.assertFailure(w.close(), LonelyError)


class Reconnection(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.create(APPID, self.relayurl, reactor)
        w1_in = []
        w1._boss._RC._debug_record_inbound_f = w1_in.append
        # w1.debug_set_trace("W1")
        w1.allocate_code()
        code = yield w1.get_code()
        w1.send_message(b"data1")  # queued until wormhole is established

        # now wait until we've deposited all our messages on the server
        def seen_our_pake():
            for m in w1_in:
                if m["type"] == "message" and m["phase"] == "pake":
                    return True
            return False

        yield poll_until(seen_our_pake)

        w1_in[:] = []
        # drop the connection
        w1._boss._RC._ws.transport.loseConnection()
        # wait for it to reconnect and redeliver all the messages. The server
        # sends mtype=message messages in random order, but we've only sent
        # one of them, so it's safe to wait for just the PAKE phase.
        yield poll_until(seen_our_pake)

        # now let the second side proceed. this simulates the most common
        # case: the server is bounced while the sender is waiting, before the
        # receiver has started

        w2 = wormhole.create(APPID, self.relayurl, reactor)
        # w2.debug_set_trace("  W2")
        w2.set_code(code)

        dataY = yield w2.get_message()
        self.assertEqual(dataY, b"data1")

        w2.send_message(b"data2")
        dataX = yield w1.get_message()
        self.assertEqual(dataX, b"data2")

        c1 = yield w1.close()
        self.assertEqual(c1, "happy")
        c2 = yield w2.close()
        self.assertEqual(c2, "happy")


class InitialFailure(unittest.TestCase):
    @inlineCallbacks
    def assertSCEFailure(self, eq, d, innerType):
        yield eq.flush()
        f = self.failureResultOf(d, ServerConnectionError)
        inner = f.value.reason
        self.assertIsInstance(inner, innerType)
        returnValue(inner)

    @inlineCallbacks
    def test_bad_dns(self):
        eq = EventualQueue(reactor)
        # point at a URL that will never connect
        w = wormhole.create(
            APPID, "ws://%%%.example.org:4000/v1", reactor, _eventual_queue=eq)
        # that should have already received an error, when it tried to
        # resolve the bogus DNS name. All API calls will return an error.

        e = yield self.assertSCEFailure(eq, w.get_unverified_key(), ValueError)
        self.assertIsInstance(e, ValueError)
        self.assertEqual(str(e), "invalid hostname: %%%.example.org")
        yield self.assertSCEFailure(eq, w.get_code(), ValueError)
        yield self.assertSCEFailure(eq, w.get_verifier(), ValueError)
        yield self.assertSCEFailure(eq, w.get_versions(), ValueError)
        yield self.assertSCEFailure(eq, w.get_message(), ValueError)

    @inlineCallbacks
    def assertSCE(self, d, innerType):
        e = yield self.assertFailure(d, ServerConnectionError)
        inner = e.reason
        self.assertIsInstance(inner, innerType)
        returnValue(inner)

    @inlineCallbacks
    def test_no_connection(self):
        # point at a URL that will never connect
        port = allocate_tcp_port()
        w = wormhole.create(APPID, "ws://127.0.0.1:%d/v1" % port, reactor)
        # nothing is listening, but it will take a turn to discover that
        d1 = w.get_code()
        d2 = w.get_unverified_key()
        d3 = w.get_verifier()
        d4 = w.get_versions()
        d5 = w.get_message()
        yield self.assertSCE(d1, ConnectionRefusedError)
        yield self.assertSCE(d2, ConnectionRefusedError)
        yield self.assertSCE(d3, ConnectionRefusedError)
        yield self.assertSCE(d4, ConnectionRefusedError)
        yield self.assertSCE(d5, ConnectionRefusedError)

    @inlineCallbacks
    def test_all_deferreds(self):
        # point at a URL that will never connect
        port = allocate_tcp_port()
        w = wormhole.create(APPID, "ws://127.0.0.1:%d/v1" % port, reactor)
        # nothing is listening, but it will take a turn to discover that
        w.allocate_code()
        d1 = w.get_code()
        d2 = w.get_unverified_key()
        d3 = w.get_verifier()
        d4 = w.get_versions()
        d5 = w.get_message()
        yield self.assertSCE(d1, ConnectionRefusedError)
        yield self.assertSCE(d2, ConnectionRefusedError)
        yield self.assertSCE(d3, ConnectionRefusedError)
        yield self.assertSCE(d4, ConnectionRefusedError)
        yield self.assertSCE(d5, ConnectionRefusedError)


class Trace(unittest.TestCase):
    def test_basic(self):
        w1 = wormhole.create(APPID, "ws://localhost:1", reactor)
        stderr = io.StringIO()
        w1.debug_set_trace("W1", file=stderr)
        # if Automat doesn't have the tracing API, then we won't actually
        # exercise the tracing function, so exercise the RendezvousConnector
        # function manually (it isn't a state machine, so it will always wire
        # up the tracer)
        w1._boss._RC._debug("what")

        stderr = io.StringIO()
        out = w1._boss._print_trace("OLD", "IN", "NEW", "C1", "M1", stderr)
        self.assertEqual(stderr.getvalue().splitlines(),
                         ["C1.M1[OLD].IN -> [NEW]"])
        out("OUT1")
        self.assertEqual(stderr.getvalue().splitlines(),
                         ["C1.M1[OLD].IN -> [NEW]", " C1.M1.OUT1()"])
        w1._boss._print_trace("", "R.connected", "", "C1", "RC1", stderr)
        self.assertEqual(
            stderr.getvalue().splitlines(),
            ["C1.M1[OLD].IN -> [NEW]", " C1.M1.OUT1()", "C1.RC1.R.connected"])

    def test_delegated(self):
        dg = Delegate()
        w1 = wormhole.create(APPID, "ws://localhost:1", reactor, delegate=dg)
        stderr = io.StringIO()
        w1.debug_set_trace("W1", file=stderr)
        w1._boss._RC._debug("what")
