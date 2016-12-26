from __future__ import print_function, unicode_literals
import os, json, re, gc
from binascii import hexlify, unhexlify
import mock
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import Deferred, gatherResults, inlineCallbacks
from .common import ServerBase
from .. import wormhole
from ..errors import (WrongPasswordError, WelcomeError, InternalError,
                      KeyFormatError)
from spake2 import SPAKE2_Symmetric
from ..timing import DebugTiming
from ..util import (bytes_to_dict, dict_to_bytes,
                    hexstr_to_bytes, bytes_to_hexstr)
from nacl.secret import SecretBox

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
        w = wormhole._WelcomeHandler("relay_url", "current_cli_version", None)
        w.handle_welcome({})

    def test_print_motd(self):
        w = wormhole._WelcomeHandler("relay_url", "current_cli_version", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({"motd": "message of\nthe day"})
        self.assertEqual(stderr.method_calls,
                         [mock.call.write("Server (at relay_url) says:\n"
                                          " message of\n the day"),
                          mock.call.write("\n")])
        # motd can be displayed multiple times
        with mock.patch("sys.stderr") as stderr2:
            w.handle_welcome({"motd": "second message"})
        self.assertEqual(stderr2.method_calls,
                         [mock.call.write("Server (at relay_url) says:\n"
                                          " second message"),
                          mock.call.write("\n")])

    def test_current_version(self):
        w = wormhole._WelcomeHandler("relay_url", "2.0", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({"current_cli_version": "2.0"})
        self.assertEqual(stderr.method_calls, [])

        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({"current_cli_version": "3.0"})
        exp1 = ("Warning: errors may occur unless both sides are"
                " running the same version")
        exp2 = ("Server claims 3.0 is current, but ours is 2.0")
        self.assertEqual(stderr.method_calls,
                         [mock.call.write(exp1),
                          mock.call.write("\n"),
                          mock.call.write(exp2),
                          mock.call.write("\n"),
                          ])

        # warning is only displayed once
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({"current_cli_version": "3.0"})
        self.assertEqual(stderr.method_calls, [])

    def test_non_release_version(self):
        w = wormhole._WelcomeHandler("relay_url", "2.0-dirty", None)
        with mock.patch("sys.stderr") as stderr:
            w.handle_welcome({"current_cli_version": "3.0"})
        self.assertEqual(stderr.method_calls, [])

    def test_signal_error(self):
        se = mock.Mock()
        w = wormhole._WelcomeHandler("relay_url", "2.0", se)
        w.handle_welcome({})
        self.assertEqual(se.mock_calls, [])

        w.handle_welcome({"error": "oops"})
        self.assertEqual(len(se.mock_calls), 1)
        self.assertEqual(len(se.mock_calls[0][1]), 2) # posargs
        we = se.mock_calls[0][1][0]
        self.assertIsInstance(we, WelcomeError)
        self.assertEqual(we.args, ("oops",))
        mood = se.mock_calls[0][1][1]
        self.assertEqual(mood, "unwelcome")
        # alas WelcomeError instances don't compare against each other
        #self.assertEqual(se.mock_calls, [mock.call(WelcomeError("oops"))])

class InputCode(unittest.TestCase):
    def test_list(self):
        send_command = mock.Mock()
        ic = wormhole._InputCode(None, "prompt", 2, send_command,
                                 DebugTiming())
        d = ic._list()
        self.assertNoResult(d)
        self.assertEqual(send_command.mock_calls, [mock.call("list")])
        ic._response_handle_nameplates({"type": "nameplates",
                                        "nameplates": [{"id": "123"}]})
        res = self.successResultOf(d)
        self.assertEqual(res, ["123"])

class GetCode(unittest.TestCase):
    def test_get(self):
        send_command = mock.Mock()
        gc = wormhole._GetCode(2, send_command, DebugTiming())
        d = gc.go()
        self.assertNoResult(d)
        self.assertEqual(send_command.mock_calls, [mock.call("allocate")])
        # TODO: nameplate attributes get added and checked here
        gc._response_handle_allocated({"type": "allocated",
                                       "nameplate": "123"})
        code = self.successResultOf(d)
        self.assertIsInstance(code, type(""))
        self.assert_(code.startswith("123-"))
        pieces = code.split("-")
        self.assertEqual(len(pieces), 3) # nameplate plus two words
        self.assert_(re.search(r'^\d+-\w+-\w+$', code), code)

class Basic(unittest.TestCase):
    def tearDown(self):
        # flush out any errorful Deferreds left dangling in cycles
        gc.collect()

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
            self.assertEqual(out[i]["type"], t, (i,t,out))
        return out

    def make_pake(self, code, side, msg1):
        sp2 = SPAKE2_Symmetric(wormhole.to_bytes(code),
                               idSymmetric=wormhole.to_bytes(APPID))
        msg2 = sp2.start()
        key = sp2.finish(msg1)
        return key, msg2

    def test_create(self):
        wormhole._Wormhole(APPID, "relay_url", reactor, None, None)

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
            w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        wh = wh_c.return_value
        self.assertEqual(w._ws_url, "relay_url")
        self.assertTrue(w._flag_need_nameplate)
        self.assertTrue(w._flag_need_to_build_msg1)
        self.assertTrue(w._flag_need_to_send_PAKE)

        v = w.verify()

        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        out = ws.outbound()
        self.assertEqual(len(out), 0)

        w._event_ws_opened(None)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type="bind", appid=APPID, side=w._side)
        self.assertIn("id", out[0])

        # WelcomeHandler should get called upon 'welcome' response. Its full
        # behavior is exercised in 'Welcome' above.
        WELCOME = {"foo": "bar"}
        response(w, type="welcome", welcome=WELCOME)
        self.assertEqual(wh.mock_calls, [mock.call.handle_welcome(WELCOME)])

        # because we're connected, setting the code also claims the mailbox
        CODE = "123-foo-bar"
        w.set_code(CODE)
        self.assertFalse(w._flag_need_to_build_msg1)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type="claim", nameplate="123")

        # the server reveals the linked mailbox
        response(w, type="claimed", mailbox="mb456")

        # that triggers event_learned_mailbox, which should send open() and
        # PAKE
        self.assertEqual(w._mailbox_state, wormhole.OPEN)
        out = ws.outbound()
        self.assertEqual(len(out), 2)
        self.check_out(out[0], type="open", mailbox="mb456")
        self.check_out(out[1], type="add", phase="pake")
        self.assertNoResult(v)

        # server echoes back all "add" messages
        response(w, type="message", phase="pake", body=out[1]["body"],
                 side=w._side)
        self.assertNoResult(v)

        # extract our outbound PAKE message
        body = bytes_to_dict(hexstr_to_bytes(out[1]["body"]))
        msg1 = hexstr_to_bytes(body["pake_v1"])

        # next we build the simulated peer's PAKE operation
        side2 = w._side + "other"
        key, msg2 = self.make_pake(CODE, side2, msg1)
        payload = {"pake_v1": bytes_to_hexstr(msg2)}
        body_hex = bytes_to_hexstr(dict_to_bytes(payload))
        response(w, type="message", phase="pake", body=body_hex, side=side2)

        # hearing the peer's PAKE (msg2) makes us release the nameplate, send
        # the confirmation message, and sends any queued phase messages. It
        # doesn't deliver the verifier because we're still waiting on the
        # confirmation message.
        self.assertFalse(w._flag_need_to_see_mailbox_used)
        self.assertEqual(w._key, key)
        out = ws.outbound()
        self.assertEqual(len(out), 2, out)
        self.check_out(out[0], type="release")
        self.check_out(out[1], type="add", phase="version")
        self.assertNoResult(v)

        # hearing a valid confirmation message doesn't throw an error
        plaintext = json.dumps({}).encode("utf-8")
        data_key = w._derive_phase_key(side2, "version")
        confmsg = w._encrypt_data(data_key, plaintext)
        version2_hex = hexlify(confmsg).decode("ascii")
        response(w, type="message", phase="version", body=version2_hex,
                 side=side2)

        # and it releases the verifier
        verifier = self.successResultOf(v)
        self.assertEqual(verifier,
                         w.derive_key("wormhole:verifier", SecretBox.KEY_SIZE))

        # an outbound message can now be sent immediately
        w.send(b"phase0-outbound")
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type="add", phase="0")
        # decrypt+check the outbound message
        p0_outbound = unhexlify(out[0]["body"].encode("ascii"))
        msgkey0 = w._derive_phase_key(w._side, "0")
        p0_plaintext = w._decrypt_data(msgkey0, p0_outbound)
        self.assertEqual(p0_plaintext, b"phase0-outbound")

        # get() waits for the inbound message to arrive
        md = w.get()
        self.assertNoResult(md)
        self.assertIn("0", w._receive_waiters)
        self.assertNotIn("0", w._received_messages)
        msgkey1 = w._derive_phase_key(side2, "0")
        p0_inbound = w._encrypt_data(msgkey1, b"phase0-inbound")
        p0_inbound_hex = hexlify(p0_inbound).decode("ascii")
        response(w, type="message", phase="0", body=p0_inbound_hex,
                 side=side2)
        p0_in = self.successResultOf(md)
        self.assertEqual(p0_in, b"phase0-inbound")
        self.assertNotIn("0", w._receive_waiters)
        self.assertIn("0", w._received_messages)

        # receiving an inbound message will queue it until get() is called
        msgkey2 = w._derive_phase_key(side2, "1")
        p1_inbound = w._encrypt_data(msgkey2, b"phase1-inbound")
        p1_inbound_hex = hexlify(p1_inbound).decode("ascii")
        response(w, type="message", phase="1", body=p1_inbound_hex,
                 side=side2)
        self.assertIn("1", w._received_messages)
        self.assertNotIn("1", w._receive_waiters)
        p1_in = self.successResultOf(w.get())
        self.assertEqual(p1_in, b"phase1-inbound")
        self.assertIn("1", w._received_messages)
        self.assertNotIn("1", w._receive_waiters)

        d = w.close()
        self.assertNoResult(d)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type="close", mood="happy")
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="released")
        self.assertEqual(w._drop_connection.mock_calls, [])
        response(w, type="closed")
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        w._ws_closed(True, None, None)
        self.assertEqual(self.successResultOf(d), None)

    def test_close_wait_0(self):
        # Close before the connection is established. The connection still
        # gets established, but it is then torn down before sending anything.
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()

        d = w.close()
        self.assertNoResult(d)

        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        self.assertNoResult(d)

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_1(self):
        # close before even claiming the nameplate
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)

        d = w.close()
        self.check_outbound(ws, ["bind"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        self.assertNoResult(d)

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_2(self):
        # Close after claiming the nameplate, but before opening the mailbox.
        # The 'claimed' response arrives before we close.
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = "123-foo-bar"
        w.set_code(CODE)
        self.check_outbound(ws, ["bind", "claim"])

        response(w, type="claimed", mailbox="mb123")

        d = w.close()
        self.check_outbound(ws, ["open", "add", "release", "close"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="released")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="closed")
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        self.assertNoResult(d)

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_3(self):
        # close after claiming the nameplate, but before opening the mailbox
        # The 'claimed' response arrives after we start to close.
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = "123-foo-bar"
        w.set_code(CODE)
        self.check_outbound(ws, ["bind", "claim"])

        d = w.close()
        response(w, type="claimed", mailbox="mb123")
        self.check_outbound(ws, ["release"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="released")
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])
        self.assertNoResult(d)

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_4(self):
        # close after both claiming the nameplate and opening the mailbox
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = "123-foo-bar"
        w.set_code(CODE)
        response(w, type="claimed", mailbox="mb456")
        self.check_outbound(ws, ["bind", "claim", "open", "add"])

        d = w.close()
        self.check_outbound(ws, ["release", "close"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="released")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="closed")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_wait_5(self):
        # close after claiming the nameplate, opening the mailbox, then
        # releasing the nameplate
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        CODE = "123-foo-bar"
        w.set_code(CODE)
        response(w, type="claimed", mailbox="mb456")

        w._key = b""
        msgkey = w._derive_phase_key("side2", "misc")
        p1_inbound = w._encrypt_data(msgkey, b"")
        p1_inbound_hex = hexlify(p1_inbound).decode("ascii")
        response(w, type="message", phase="misc", side="side2",
                 body=p1_inbound_hex)
        self.check_outbound(ws, ["bind", "claim", "open", "add",
                                 "release"])

        d = w.close()
        self.check_outbound(ws, ["close"])
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="released")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [])

        response(w, type="closed")
        self.assertNoResult(d)
        self.assertEqual(w._drop_connection.mock_calls, [mock.call()])

        w._ws_closed(True, None, None)
        self.successResultOf(d)

    def test_close_errbacks(self):
        # make sure the Deferreds returned by verify() and get() are properly
        # errbacked upon close
        pass

    def test_get_code_mock(self):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        ws = MockWebSocket() # TODO: mock w._ws_send_command instead
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.check_outbound(ws, ["bind"])

        gc_c = mock.Mock()
        gc = gc_c.return_value = mock.Mock()
        gc_d = gc.go.return_value = Deferred()
        with mock.patch("wormhole.wormhole._GetCode", gc_c):
            d = w.get_code()
        self.assertNoResult(d)

        gc_d.callback("123-foo-bar")
        code = self.successResultOf(d)
        self.assertEqual(code, "123-foo-bar")

    def test_get_code_real(self):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.check_outbound(ws, ["bind"])

        d = w.get_code()

        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.check_out(out[0], type="allocate")
        # TODO: nameplate attributes go here
        self.assertNoResult(d)

        response(w, type="allocated", nameplate="123")
        code = self.successResultOf(d)
        self.assertIsInstance(code, type(""))
        self.assert_(code.startswith("123-"))
        pieces = code.split("-")
        self.assertEqual(len(pieces), 3) # nameplate plus two words
        self.assert_(re.search(r'^\d+-\w+-\w+$', code), code)

    def _test_establish_key_hook(self, established, before):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)

        if before:
            d = w.establish_key()

        if established is True:
            w._key = b"key"
        elif established is False:
            w._key = None
        else:
            w._key = b"key"
            w._error = WelcomeError()

        if not before:
            d = w.establish_key()
        else:
            w._maybe_notify_key()

        if w._key is not None and established is True:
            self.successResultOf(d)
        elif established is False:
            self.assertNot(d.called)
        else:
            self.failureResultOf(d)

    def test_establish_key_hook(self):
        for established in (True, False, "error"):
            for before in (True, False):
                self._test_establish_key_hook(established, before)

    def test_establish_key_twice(self):
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        d = w.establish_key()
        self.assertRaises(InternalError, w.establish_key)
        del d

    # make sure verify() can be called both before and after the verifier is
    # computed

    def _test_verifier(self, when, order, success):
        assert when in ("early", "middle", "late")
        assert order in ("key-then-version", "version-then-key")
        assert isinstance(success, bool)
        #print(when, order, success)

        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        w._ws_send_command = mock.Mock()
        w._mailbox_state = wormhole.OPEN
        side2 = "side2"
        d = None

        if success:
            w._key = b"key"
        else:
            w._key = b"wrongkey"
        plaintext = json.dumps({}).encode("utf-8")
        data_key = w._derive_phase_key(side2, "version")
        confmsg = w._encrypt_data(data_key, plaintext)
        w._key = None

        if when == "early":
            d = w.verify()
            self.assertNoResult(d)

        if order == "key-then-version":
            w._key = b"key"
            w._event_established_key()
        else:
            w._event_received_version(side2, confmsg)

        if when == "middle":
            d = w.verify()
        if d:
            self.assertNoResult(d) # still waiting for other msg

        if order == "version-then-key":
            w._key = b"key"
            w._event_established_key()
        else:
            w._event_received_version(side2, confmsg)

        if when == "late":
            d = w.verify()
        if success:
            self.successResultOf(d)
        else:
            self.assertFailure(d, wormhole.WrongPasswordError)
            self.flushLoggedErrors(WrongPasswordError)

    def test_verifier(self):
        for when in ("early", "middle", "late"):
            for order in ("key-then-version", "version-then-key"):
                for success in (False, True):
                    self._test_verifier(when, order, success)


    def test_api_errors(self):
        # doing things you're not supposed to do
        pass

    def test_welcome_error(self):
        # A welcome message could arrive at any time, with an [error] key
        # that should make us halt. In practice, though, this gets sent as
        # soon as the connection is established, which limits the possible
        # states in which we might see it.

        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        self.check_outbound(ws, ["bind"])

        d1 = w.get()
        d2 = w.verify()
        d3 = w.get_code()
        # TODO (tricky): test w.input_code

        self.assertNoResult(d1)
        self.assertNoResult(d2)
        self.assertNoResult(d3)

        w._signal_error(WelcomeError("you are not actually welcome"), "pouty")
        self.failureResultOf(d1, WelcomeError)
        self.failureResultOf(d2, WelcomeError)
        self.failureResultOf(d3, WelcomeError)

        # once the error is signalled, all API calls should fail
        self.assertRaises(WelcomeError, w.send, "foo")
        self.assertRaises(WelcomeError,
                          w.derive_key, "foo", SecretBox.KEY_SIZE)
        self.failureResultOf(w.get(), WelcomeError)
        self.failureResultOf(w.verify(), WelcomeError)

    def test_version_error(self):
        # we should only receive the "version" message after we receive the
        # PAKE message, by which point we should know the key. If the
        # confirmation message doesn't decrypt, we signal an error.
        timing = DebugTiming()
        w = wormhole._Wormhole(APPID, "relay_url", reactor, None, timing)
        w._drop_connection = mock.Mock()
        ws = MockWebSocket()
        w._event_connected(ws)
        w._event_ws_opened(None)
        w.set_code("123-foo-bar")
        response(w, type="claimed", mailbox="mb456")

        d1 = w.get()
        d2 = w.verify()
        self.assertNoResult(d1)
        self.assertNoResult(d2)

        out = ws.outbound()
        # ["bind", "claim", "open", "add"]
        self.assertEqual(len(out), 4)
        self.assertEqual(out[3]["type"], "add")

        sp2 = SPAKE2_Symmetric(b"", idSymmetric=wormhole.to_bytes(APPID))
        msg2 = sp2.start()
        payload = {"pake_v1": bytes_to_hexstr(msg2)}
        body_hex = bytes_to_hexstr(dict_to_bytes(payload))
        response(w, type="message", phase="pake", body=body_hex, side="s2")
        self.assertNoResult(d1)
        self.assertNoResult(d2) # verify() waits for confirmation

        # sending a random version message will cause a confirmation error
        confkey = w.derive_key("WRONG", SecretBox.KEY_SIZE)
        nonce = os.urandom(wormhole.CONFMSG_NONCE_LENGTH)
        badversion = wormhole.make_confmsg(confkey, nonce)
        badversion_hex = hexlify(badversion).decode("ascii")
        response(w, type="message", phase="version", body=badversion_hex,
                 side="s2")

        self.failureResultOf(d1, WrongPasswordError)
        self.failureResultOf(d2, WrongPasswordError)

        # once the error is signalled, all API calls should fail
        self.assertRaises(WrongPasswordError, w.send, "foo")
        self.assertRaises(WrongPasswordError,
                          w.derive_key, "foo", SecretBox.KEY_SIZE)
        self.failureResultOf(w.get(), WrongPasswordError)
        self.failureResultOf(w.verify(), WrongPasswordError)


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
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_same_message(self):
        # the two sides use random nonces for their messages, so it's ok for
        # both to try and send the same body: they'll result in distinct
        # encrypted messages
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send(b"data")
        w2.send(b"data")
        dataX = yield w1.get()
        dataY = yield w2.get()
        self.assertEqual(dataX, b"data")
        self.assertEqual(dataY, b"data")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_interleaved(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send(b"data1")
        dataY = yield w2.get()
        self.assertEqual(dataY, b"data1")
        d = w1.get()
        w2.send(b"data2")
        dataX = yield d
        self.assertEqual(dataX, b"data2")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_unidirectional(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code)
        w1.send(b"data1")
        dataY = yield w2.get()
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_early(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w1.send(b"data1")
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        d = w2.get()
        w1.set_code("123-abc-def")
        w2.set_code("123-abc-def")
        dataY = yield d
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_fixed_code(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()


    @inlineCallbacks
    def test_multiple_messages(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        w1.send(b"data3"), w2.send(b"data4")
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data4")
        self.assertEqual(dataY, b"data3")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_wrong_password(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code+"not")
        # That's enough to allow both sides to discover the mismatch, but
        # only after the confirmation message gets through. API calls that
        # don't wait will appear to work until the mismatched confirmation
        # message arrives.
        w1.send(b"should still work")
        w2.send(b"should still work")

        # API calls that wait (i.e. get) will errback
        yield self.assertFailure(w2.get(), WrongPasswordError)
        yield self.assertFailure(w1.get(), WrongPasswordError)

        yield w1.close()
        yield w2.close()
        self.flushLoggedErrors(WrongPasswordError)

    @inlineCallbacks
    def test_wrong_password_with_spaces(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        code_no_dashes = code.replace('-', ' ')

        with self.assertRaises(KeyFormatError) as ex:
            w2.set_code(code_no_dashes)

        expected_msg = "code (%s) contains spaces." % (code_no_dashes,)
        self.assertEqual(expected_msg, str(ex.exception))

        yield w1.close()
        yield w2.close()
        self.flushLoggedErrors(KeyFormatError)

    @inlineCallbacks
    def test_verifier(self):
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        code = yield w1.get_code()
        w2.set_code(code)
        v1 = yield w1.verify()
        v2 = yield w2.verify()
        self.failUnlessEqual(type(v1), type(b""))
        self.failUnlessEqual(v1, v2)
        w1.send(b"data1")
        w2.send(b"data2")
        dataX = yield w1.get()
        dataY = yield w2.get()
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

    @inlineCallbacks
    def test_versions(self):
        # there's no API for this yet, but make sure the internals work
        w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w1._my_versions = {"w1": 123}
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2._my_versions = {"w2": 456}
        code = yield w1.get_code()
        w2.set_code(code)
        yield w1.verify()
        self.assertEqual(w1._their_versions, {"w2": 456})
        yield w2.verify()
        self.assertEqual(w2._their_versions, {"w1": 123})
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
        # clear exception).
        with mock.patch("wormhole.wormhole._Wormhole", MessageDoublingReceiver):
            w1 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w2 = wormhole.wormhole(APPID, self.relayurl, reactor)
        w1.set_code("123-purple-elephant")
        w2.set_code("123-purple-elephant")
        w1.send(b"data1"), w2.send(b"data2")
        dl = yield self.doBoth(w1.get(), w2.get())
        (dataX, dataY) = dl
        self.assertEqual(dataX, b"data2")
        self.assertEqual(dataY, b"data1")
        yield w1.close()
        yield w2.close()

class MessageDoublingReceiver(wormhole._Wormhole):
    # we could double messages on the sending side, but a future server will
    # strip those duplicates, so to really exercise the receiver, we must
    # double them on the inbound side instead
    #def _msg_send(self, phase, body):
    #    wormhole._Wormhole._msg_send(self, phase, body)
    #    self._ws_send_command("add", phase=phase, body=bytes_to_hexstr(body))
    def _event_received_peer_message(self, side, phase, body):
        wormhole._Wormhole._event_received_peer_message(self, side, phase, body)
        wormhole._Wormhole._event_received_peer_message(self, side, phase, body)

class Errors(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_codes_1(self):
        w = wormhole.wormhole(APPID, self.relayurl, reactor)
        # definitely too early
        self.assertRaises(InternalError, w.derive_key, "purpose", 12)

        w.set_code("123-purple-elephant")
        # code can only be set once
        self.assertRaises(InternalError, w.set_code, "123-nope")
        yield self.assertFailure(w.get_code(), InternalError)
        yield self.assertFailure(w.input_code(), InternalError)
        yield w.close()

    @inlineCallbacks
    def test_codes_2(self):
        w = wormhole.wormhole(APPID, self.relayurl, reactor)
        yield w.get_code()
        self.assertRaises(InternalError, w.set_code, "123-nope")
        yield self.assertFailure(w.get_code(), InternalError)
        yield self.assertFailure(w.input_code(), InternalError)
        yield w.close()
