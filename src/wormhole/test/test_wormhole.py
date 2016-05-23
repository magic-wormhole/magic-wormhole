from __future__ import print_function
import os, json, re
from binascii import hexlify, unhexlify
import mock
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import Deferred, gatherResults, inlineCallbacks
from .common import ServerBase
from .. import wormhole
from spake2 import SPAKE2_Symmetric
from ..timing import DebugTiming

APPID = u"appid"

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
        w = wormhole._WelcomeHandler(u"relay_url", u"current_version", None)
        w.handle_welcome({})

    def test_print_motd(self):
        w = wormhole._WelcomeHandler(u"relay_url", u"current_version", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({u"motd": u"message of\nthe day"})
        self.assertEqual(stderr.method_calls,
                         [mock.call.write(u"Server (at relay_url) says:\n"
                                          " message of\n the day"),
                          mock.call.write(u"\n")])
        # motd is only displayed once
        with mock.patch("sys.stderr") as stderr2:
            w.handle_welcome({u"motd": u"second message"})
        self.assertEqual(stderr2.method_calls, [])

    def test_current_version(self):
        w = wormhole._WelcomeHandler(u"relay_url", u"2.0", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({u"current_version": u"2.0"})
        self.assertEqual(stderr.method_calls, [])

        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({u"current_version": u"3.0"})
        exp1 = (u"Warning: errors may occur unless both sides are"
                " running the same version")
        exp2 = (u"Server claims 3.0 is current, but ours is 2.0")
        self.assertEqual(stderr.method_calls,
                         [mock.call.write(exp1),
                          mock.call.write(u"\n"),
                          mock.call.write(exp2),
                          mock.call.write(u"\n"),
                          ])

        # warning is only displayed once
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({u"current_version": u"3.0"})
        self.assertEqual(stderr.method_calls, [])

    def test_non_release_version(self):
        w = wormhole._WelcomeHandler(u"relay_url", u"2.0-dirty", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({u"current_version": u"3.0"})
        self.assertEqual(stderr.method_calls, [])

    def test_signal_error(self):
        se = mock.Mock()
        w = wormhole._WelcomeHandler(u"relay_url", u"2.0", se)
        w.handle_welcome({})
        self.assertEqual(se.mock_calls, [])

        w.handle_welcome({u"error": u"oops"})
        self.assertEqual(se.mock_calls, [mock.call(u"oops")])

class InputCode(unittest.TestCase):
    def test_list(self):
        send_command = mock.Mock()
        ic = wormhole._InputCode(None, u"prompt", 2, send_command,
                                 DebugTiming())
        d = ic._list()
        self.assertNoResult(d)
        self.assertEqual(send_command.mock_calls, [mock.call(u"list")])
        ic._response_handle_nameplates({u"type": u"nameplates",
                                        u"nameplates": [{u"id": u"123"}]})
        res = self.successResultOf(d)
        self.assertEqual(res, [u"123"])

class GetCode(unittest.TestCase):
    def test_get(self):
        send_command = mock.Mock()
        gc = wormhole._GetCode(2, send_command, DebugTiming())
        d = gc.go()
        self.assertNoResult(d)
        self.assertEqual(send_command.mock_calls, [mock.call(u"allocate")])
        # TODO: nameplate attributes get added and checked here
        gc._response_handle_allocated({u"type": u"allocated",
                                       u"nameplate": u"123"})
        code = self.successResultOf(d)
        self.assertIsInstance(code, type(u""))
        self.assert_(code.startswith(u"123-"))
        pieces = code.split(u"-")
        self.assertEqual(len(pieces), 3) # nameplate plus two words
        self.assert_(re.search(r'^\d+-\w+-\w+$', code), code)


class Basic(unittest.TestCase):
    def test_create(self):
        wormhole._Wormhole(APPID, u"relay_url", reactor, None, None)

    def check_out(self, out, **kwargs):
        # Assert that each kwarg is present in the 'out' dict. Ignore other
        # keys ('msgid' in particular)
        for key, value in kwargs.items():
            self.assertIn(key, out)
            self.assertEqual(out[key], value, (out, key, value))

    def check_outbound(self, ws, types):
        out = ws.outbound()
        self.assertEqual(len(out), len(types), (out, types))
        for i,t in enumerate(types):
            self.assertEqual(out[i][u"type"], t, (i,t,out))
        return out

    def test_basic(self):
        # We don't call w._start(), so this doesn't create a WebSocket
        # connection. We provide a mock connection instead. If we wanted to
        # exercise _connect, we'd mock out WSFactory.
        # w._connect = lambda self: None
        # w._event_connected(mock_ws)
        # w._event_ws_opened()
        # w._ws_dispatch_response(payload)

        timing = DebugTiming()
        with mock.patch("wormhole.wormhole._WelcomeHandler") as wh_c:
            w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        wh = wh_c.return_value
        self.assertEqual(w._ws_url, u"relay_url")
        self.assertTrue(w._flag_need_nameplate)
        self.assertTrue(w._flag_need_to_build_msg1)
        self.assertTrue(w._flag_need_to_send_PAKE)

        v = w.get_verifier()

        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        out = ws.outbound()
        self.assertEqual(len(out), 0)

        w._event_ws_opened(None)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type=u"bind", appid=APPID, side=w._side)
        self.assertIn(u"id", out[0])

        # WelcomeHandler should get called upon 'welcome' response. Its full
        # behavior is exercised in 'Welcome' above.
        WELCOME = {u"foo": u"bar"}
        response(w, type="welcome", welcome=WELCOME)
        self.assertEqual(wh.mock_calls, [mock.call.handle_welcome(WELCOME)])

        # because we're connected, setting the code also claims the mailbox
        CODE = u"123-foo-bar"
        w.set_code(CODE)
        self.assertFalse(w._flag_need_to_build_msg1)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type=u"claim", nameplate=u"123")

        # the server reveals the linked mailbox
        response(w, type=u"claimed", mailbox=u"mb456")

        # that triggers event_learned_mailbox, which should send open() and
        # PAKE
        self.assertTrue(w._mailbox_opened)
        out = ws.outbound()
        self.assertEqual(len(out), 2)
        self.check_out(out[0], type=u"open", mailbox=u"mb456")
        self.check_out(out[1], type=u"add", phase=u"pake")
        self.assertNoResult(v)

        # server echoes back all "add" messages
        response(w, type=u"message", phase=u"pake", body=out[1][u"body"],
                 side=w._side)
        self.assertNoResult(v)

        # next we build the simulated peer's PAKE operation
        side2 = w._side + u"other"
        msg1 = unhexlify(out[1][u"body"].encode("ascii"))
        sp2 = SPAKE2_Symmetric(wormhole.to_bytes(CODE),
                               idSymmetric=wormhole.to_bytes(APPID))
        msg2 = sp2.start()
        msg2_hex = hexlify(msg2).decode("ascii")
        key = sp2.finish(msg1)
        response(w, type=u"message", phase=u"pake", body=msg2_hex, side=side2)

        # hearing the peer's PAKE (msg2) makes us release the nameplate, send
        # the confirmation message, delivered the verifier, and sends any
        # queued phase messages
        self.assertFalse(w._flag_need_to_see_mailbox_used)
        self.assertEqual(w._key, key)
        out = ws.outbound()
        self.assertEqual(len(out), 2, out)
        self.check_out(out[0], type=u"release")
        self.check_out(out[1], type=u"add", phase=u"confirm")
        verifier = self.successResultOf(v)
        self.assertEqual(verifier, w.derive_key(u"wormhole:verifier"))

        # hearing a valid confirmation message doesn't throw an error
        confkey = w.derive_key(u"wormhole:confirmation")
        nonce = os.urandom(wormhole.CONFMSG_NONCE_LENGTH)
        confirm2 = wormhole.make_confmsg(confkey, nonce)
        confirm2_hex = hexlify(confirm2).decode("ascii")
        response(w, type=u"message", phase=u"confirm", body=confirm2_hex,
                 side=side2)

        # an outbound message can now be sent immediately
        w.send(b"phase0-outbound")
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type=u"add", phase=u"0")
        # decrypt+check the outbound message
        p0_outbound = unhexlify(out[0][u"body"].encode("ascii"))
        msgkey0 = w.derive_key(u"wormhole:phase:0")
        p0_plaintext = w._decrypt_data(msgkey0, p0_outbound)
        self.assertEqual(p0_plaintext, b"phase0-outbound")

        # get() waits for the inbound message to arrive
        md = w.get()
        self.assertNoResult(md)
        self.assertIn(u"0", w._receive_waiters)
        self.assertNotIn(u"0", w._received_messages)
        p0_inbound = w._encrypt_data(msgkey0, b"phase0-inbound")
        p0_inbound_hex = hexlify(p0_inbound).decode("ascii")
        response(w, type=u"message", phase=u"0", body=p0_inbound_hex,
                 side=side2)
        p0_in = self.successResultOf(md)
        self.assertEqual(p0_in, b"phase0-inbound")
        self.assertNotIn(u"0", w._receive_waiters)
        self.assertIn(u"0", w._received_messages)

        # receiving an inbound message will queue it until get() is called
        msgkey1 = w.derive_key(u"wormhole:phase:1")
        p1_inbound = w._encrypt_data(msgkey1, b"phase1-inbound")
        p1_inbound_hex = hexlify(p1_inbound).decode("ascii")
        response(w, type=u"message", phase=u"1", body=p1_inbound_hex,
                 side=side2)
        self.assertIn(u"1", w._received_messages)
        self.assertNotIn(u"1", w._receive_waiters)
        p1_in = self.successResultOf(w.get())
        self.assertEqual(p1_in, b"phase1-inbound")
        self.assertIn(u"1", w._received_messages)
        self.assertNotIn(u"1", w._receive_waiters)

        w.close()
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type=u"close", mood=u"happy")
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])

    def test_close_wait_1(self):
        # close after claiming the nameplate, but before opening the mailbox
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = u"123-foo-bar"
        w.set_code(CODE)
        self.check_outbound(ws, [u"bind", u"claim"])

        d = w.close(wait=True)
        self.check_outbound(ws, [u"release"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type=u"released")
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        self.assertNoResult(d)

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_2(self):
        # close after both claiming the nameplate and opening the mailbox
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = u"123-foo-bar"
        w.set_code(CODE)
        response(w, type=u"claimed", mailbox=u"mb456")
        self.check_outbound(ws, [u"bind", u"claim", u"open", u"add"])

        d = w.close(wait=True)
        self.check_outbound(ws, [u"release", u"close"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type=u"released")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type=u"closed")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_3(self):
        # close after claiming the nameplate, opening the mailbox, then
        # releasing the nameplate
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = u"123-foo-bar"
        w.set_code(CODE)
        response(w, type=u"claimed", mailbox=u"mb456")

        w._key = b""
        msgkey = w.derive_key(u"wormhole:phase:misc")
        p1_inbound = w._encrypt_data(msgkey, b"")
        p1_inbound_hex = hexlify(p1_inbound).decode("ascii")
        response(w, type=u"message", phase=u"misc", side=u"side2",
                 body=p1_inbound_hex)
        self.check_outbound(ws, [u"bind", u"claim", u"open", u"add",
                                 u"release"])

        d = w.close(wait=True)
        self.check_outbound(ws, [u"close"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type=u"released")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type=u"closed")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_get_code_mock(self):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        ws = MockWebSocket() # TODO: mock w._ws_send_command instead
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.check_outbound(ws, [u"bind"])

        gc_c = mock.Mock()
        gc = gc_c.return_value = mock.Mock()
        gc_d = gc.go.return_value = Deferred()
        with mock.patch("wormhole.wormhole._GetCode", gc_c):
            d = w.get_code()
        self.assertNoResult(d)

        gc_d.callback(u"123-foo-bar")
        code = self.successResultOf(d)
        self.assertEqual(code, u"123-foo-bar")

    def test_get_code_real(self):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, u"relay_url", reactor, None, timing)
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.check_outbound(ws, [u"bind"])

        d = w.get_code()

        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type=u"allocate")
        # TODO: nameplate attributes go here
        self.assertNoResult(d)

        response(w, type=u"allocated", nameplate=u"123")
        code = self.successResultOf(d)
        self.assertIsInstance(code, type(u""))
        self.assert_(code.startswith(u"123-"))
        pieces = code.split(u"-")
        self.assertEqual(len(pieces), 3) # nameplate plus two words
        self.assert_(re.search(r'^\d+-\w+-\w+$', code), code)

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

class Wormholes(ServerBase, unittest.TestCase):
    # integration test, with a real server

    def doBoth(self, d1, d2):
        return gatherResults([d1, d2], True)

    @inlineCallbacks
    def test_basic(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send(b"data1")
        w2.send(b"data2")
        dataX = yield w1.get()
        dataY = yield w2.get()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close(wait=True)
        yield w2.close(wait=True)

class Off:

    @inlineCallbacks
    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        yield self.doBoth(w1.send(b"data"), w2.send(b"data"))
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data")
        self.assertEqual(dataY, b"data")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_interleaved(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        res = yield self.doBoth(w1.send(b"data1"), w2.get())
        (_, dataY) = res
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get(), w2.send(b"data2"))
        (dataX, _) = dl
        self.assertEqual(dataX, b"data2")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_fixed_code(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        yield self.doBoth(w1.send(b"data1"), w2.send(b"data2"))
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield self.doBoth(w1.close(), w2.close())


    @inlineCallbacks
    def test_multiple_messages(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        yield self.doBoth(w1.send(b"data1"), w2.send(b"data2"))
        yield self.doBoth(w1.send(b"data3"), w2.send(b"data4"))
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data4")
        self.assertEqual(dataY, b"data3")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_multiple_messages_2(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        w1.set_code(u"123-purple-elephant")
        w2.set_code(u"123-purple-elephant")
        # TODO: set_code should be sufficient to kick things off, but for now
        # we must also let both sides do at least one send() or get()
        yield self.doBoth(w1.send(b"data1"), w2.send(b"ignored"))
        yield w1.get()
        yield w1.send(b"data2")
        yield w1.send(b"data3")
        data = yield w2.get()
        self.assertEqual(data, b"data1")
        data = yield w2.get()
        self.assertEqual(data, b"data2")
        data = yield w2.get()
        self.assertEqual(data, b"data3")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_wrong_password(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code+"not")

        # w2 can't throw WrongPasswordError until it sees a CONFIRM message,
        # and w1 won't send CONFIRM until it sees a PAKE message, which w2
        # won't send until we call get. So we need both sides to be
        # running at the same time for this test.
        d1 = w1.send(b"data1")
        # at this point, w1 should be waiting for w2.PAKE

        yield self.assertFailure(w2.get(), WrongPasswordError)
        # * w2 will send w2.PAKE, wait for (and get) w1.PAKE, compute a key,
        #   send w2.CONFIRM, then wait for w1.DATA.
        # * w1 will get w2.PAKE, compute a key, send w1.CONFIRM.
        # * w1 might also get w2.CONFIRM, and may notice the error before it
        #   sends w1.CONFIRM, in which case the wait=True will signal an
        #   error inside _get_master_key() (inside send), and d1 will
        #   errback.
        #   * but w1 might not see w2.CONFIRM yet, in which case it won't
        #     errback until we do w1.get()
        # * w2 gets w1.CONFIRM, notices the error, records it.
        # * w2 (waiting for w1.DATA) wakes up, sees the error, throws
        # * meanwhile w1 finishes sending its data. w2.CONFIRM may or may not
        #   have arrived by then
        try:
            yield d1
        except WrongPasswordError:
            pass

        # When we ask w1 to get(), one of two things might happen:
        # * if w2.CONFIRM arrived already, it will have recorded the error.
        #   When w1.get() sleeps (waiting for w2.DATA), we'll notice the
        #   error before sleeping, and throw WrongPasswordError
        # * if w2.CONFIRM hasn't arrived yet, we'll sleep. When w2.CONFIRM
        #   arrives, we notice and record the error, and wake up, and throw

        # Note that we didn't do w2.send(), so we're hoping that w1 will
        # have enough information to detect the error before it sleeps
        # (waiting for w2.DATA). Checking for the error both before sleeping
        # and after waking up makes this happen.

        # so now w1 should have enough information to throw too
        yield self.assertFailure(w1.get(), WrongPasswordError)

        # both sides are closed automatically upon error, but it's still
        # legal to call .close(), and should be idempotent
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_no_confirm(self):
        # newer versions (which check confirmations) should will work with
        # older versions (that don't send confirmations)
        w1 = Wormhole(APPID, self.relayurl)
        w1._send_confirm = False
        w2 = Wormhole(APPID, self.relayurl)

        code = yield w1.get_code()
        w2.set_code(code)
        dl = yield self.doBoth(w1.send(b"data1"), w2.get())
        self.assertEqual(dl[1], b"data1")
        dl = yield self.doBoth(w1.get(), w2.send(b"data2"))
        self.assertEqual(dl[0], b"data2")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_verifier(self):
        w1 = Wormhole(APPID, self.relayurl)
        w2 = Wormhole(APPID, self.relayurl)
        code = yield w1.get_code()
        w2.set_code(code)
        res = yield self.doBoth(w1.get_verifier(), w2.get_verifier())
        v1, v2 = res
        self.failUnlessEqual(type(v1), type(b""))
        self.failUnlessEqual(v1, v2)
        yield self.doBoth(w1.send(b"data1"), w2.send(b"data2"))
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield self.doBoth(w1.close(), w2.close())

    @inlineCallbacks
    def test_errors(self):
        w1 = Wormhole(APPID, self.relayurl)
        yield self.assertFailure(w1.get_verifier(), UsageError)
        yield self.assertFailure(w1.send(b"data"), UsageError)
        yield self.assertFailure(w1.get(), UsageError)
        w1.set_code(u"123-purple-elephant")
        yield self.assertRaises(UsageError, w1.set_code, u"123-nope")
        yield self.assertFailure(w1.get_code(), UsageError)
        w2 = Wormhole(APPID, self.relayurl)
        yield w2.get_code()
        yield self.assertFailure(w2.get_code(), UsageError)
        yield self.doBoth(w1.close(), w2.close())

