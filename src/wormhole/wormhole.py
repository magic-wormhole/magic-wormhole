from __future__ import print_function, absolute_import, unicode_literals
import os, sys, re
from six.moves.urllib_parse import urlparse
from twisted.internet import defer, endpoints, error
from twisted.internet.threads import deferToThread, blockingCallFromThread
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log, failure
from autobahn.twisted import websocket
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from spake2 import SPAKE2_Symmetric
from hashlib import sha256
from . import __version__
from . import codes
#from .errors import ServerError, Timeout
from .errors import (WrongPasswordError, InternalError, WelcomeError,
                     WormholeClosedError, KeyFormatError)
from .timing import DebugTiming
from .util import (to_bytes, bytes_to_hexstr, hexstr_to_bytes,
                   dict_to_bytes, bytes_to_dict)
from hkdf import Hkdf

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

CONFMSG_NONCE_LENGTH = 128//8
CONFMSG_MAC_LENGTH = 256//8
def make_confmsg(confkey, nonce):
    return nonce+HKDF(confkey, CONFMSG_MAC_LENGTH, nonce)


# We send the following messages through the relay server to the far side (by
# sending "add" commands to the server, and getting "message" responses):
#
# phase=setup:
#   * unauthenticated version strings (but why?)
#   * early warmup for connection hints ("I can do tor, spin up HS")
#   * wordlist l10n identifier
# phase=pake: just the SPAKE2 'start' message (binary)
# phase=version: version data, key verification (HKDF(key, nonce)+nonce)
# phase=1,2,3,..: application messages

class WSClient(websocket.WebSocketClientProtocol):
    def onOpen(self):
        self.wormhole_open = True
        self.factory.d.callback(self)

    def onMessage(self, payload, isBinary):
        assert not isBinary
        self.wormhole._ws_dispatch_response(payload)

    def onClose(self, wasClean, code, reason):
        if self.wormhole_open:
            self.wormhole._ws_closed(wasClean, code, reason)
        else:
            # we closed before establishing a connection (onConnect) or
            # finishing WebSocket negotiation (onOpen): errback
            self.factory.d.errback(error.ConnectError(reason))

class WSFactory(websocket.WebSocketClientFactory):
    protocol = WSClient
    def buildProtocol(self, addr):
        proto = websocket.WebSocketClientFactory.buildProtocol(self, addr)
        proto.wormhole = self.wormhole
        proto.wormhole_open = False
        return proto


class _GetCode:
    def __init__(self, code_length, send_command, timing):
        self._code_length = code_length
        self._send_command = send_command
        self._timing = timing
        self._allocated_d = defer.Deferred()

    @inlineCallbacks
    def go(self):
        with self._timing.add("allocate"):
            self._send_command("allocate")
            nameplate_id = yield self._allocated_d
        code = codes.make_code(nameplate_id, self._code_length)
        assert isinstance(code, type("")), type(code)
        returnValue(code)

    def _response_handle_allocated(self, msg):
        nid = msg["nameplate"]
        assert isinstance(nid, type("")), type(nid)
        self._allocated_d.callback(nid)

class _InputCode:
    def __init__(self, reactor, prompt, code_length, send_command, timing):
        self._reactor = reactor
        self._prompt = prompt
        self._code_length = code_length
        self._send_command = send_command
        self._timing = timing

    @inlineCallbacks
    def _list(self):
        self._lister_d = defer.Deferred()
        self._send_command("list")
        nameplates = yield self._lister_d
        self._lister_d = None
        returnValue(nameplates)

    def _list_blocking(self):
        return blockingCallFromThread(self._reactor, self._list)

    @inlineCallbacks
    def go(self):
        # fetch the list of nameplates ahead of time, to give us a chance to
        # discover the welcome message (and warn the user about an obsolete
        # client)
        #
        # TODO: send the request early, show the prompt right away, hide the
        # latency in the user's indecision and slow typing. If we're lucky
        # the answer will come back before they hit TAB.

        initial_nameplate_ids = yield self._list()
        with self._timing.add("input code", waiting="user"):
            t = self._reactor.addSystemEventTrigger("before", "shutdown",
                                                    self._warn_readline)
            code = yield deferToThread(codes.input_code_with_completion,
                                       self._prompt,
                                       initial_nameplate_ids,
                                       self._list_blocking,
                                       self._code_length)
            self._reactor.removeSystemEventTrigger(t)
        returnValue(code)

    def _response_handle_nameplates(self, msg):
        nameplates = msg["nameplates"]
        assert isinstance(nameplates, list), type(nameplates)
        nids = []
        for n in nameplates:
            assert isinstance(n, dict), type(n)
            nameplate_id = n["id"]
            assert isinstance(nameplate_id, type("")), type(nameplate_id)
            nids.append(nameplate_id)
        self._lister_d.callback(nids)

    def _warn_readline(self):
        # When our process receives a SIGINT, Twisted's SIGINT handler will
        # stop the reactor and wait for all threads to terminate before the
        # process exits. However, if we were waiting for
        # input_code_with_completion() when SIGINT happened, the readline
        # thread will be blocked waiting for something on stdin. Trick the
        # user into satisfying the blocking read so we can exit.
        print("\nCommand interrupted: please press Return to quit",
              file=sys.stderr)

        # Other potential approaches to this problem:
        # * hard-terminate our process with os._exit(1), but make sure the
        #   tty gets reset to a normal mode ("cooked"?) first, so that the
        #   next shell command the user types is echoed correctly
        # * track down the thread (t.p.threadable.getThreadID from inside the
        #   thread), get a cffi binding to pthread_kill, deliver SIGINT to it
        # * allocate a pty pair (pty.openpty), replace sys.stdin with the
        #   slave, build a pty bridge that copies bytes (and other PTY
        #   things) from the real stdin to the master, then close the slave
        #   at shutdown, so readline sees EOF
        # * write tab-completion and basic editing (TTY raw mode,
        #   backspace-is-erase) without readline, probably with curses or
        #   twisted.conch.insults
        # * write a separate program to get codes (maybe just "wormhole
        #   --internal-get-code"), run it as a subprocess, let it inherit
        #   stdin/stdout, send it SIGINT when we receive SIGINT ourselves. It
        #   needs an RPC mechanism (over some extra file descriptors) to ask
        #   us to fetch the current nameplate_id list.
        #
        # Note that hard-terminating our process with os.kill(os.getpid(),
        # signal.SIGKILL), or SIGTERM, doesn't seem to work: the thread
        # doesn't see the signal, and we must still wait for stdin to make
        # readline finish.

class _WelcomeHandler:
    def __init__(self, url, current_version, signal_error):
        self._ws_url = url
        self._version_warning_displayed = False
        self._current_version = current_version
        self._signal_error = signal_error

    def handle_welcome(self, welcome):
        if "motd" in welcome:
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" %
                  (self._ws_url, motd_formatted), file=sys.stderr)

        # Only warn if we're running a release version (e.g. 0.0.6, not
        # 0.0.6-DISTANCE-gHASH). Only warn once.
        if ("current_cli_version" in welcome
            and "-" not in self._current_version
            and not self._version_warning_displayed
            and welcome["current_cli_version"] != self._current_version):
            print("Warning: errors may occur unless both sides are running the same version", file=sys.stderr)
            print("Server claims %s is current, but ours is %s"
                  % (welcome["current_cli_version"], self._current_version),
                  file=sys.stderr)
            self._version_warning_displayed = True

        if "error" in welcome:
            return self._signal_error(WelcomeError(welcome["error"]),
                                      "unwelcome")

# states for nameplates, mailboxes, and the websocket connection
(CLOSED, OPENING, OPEN, CLOSING) = ("closed", "opening", "open", "closing")


class _Wormhole:
    DEBUG = False

    def __init__(self, appid, relay_url, reactor, tor_manager, timing):
        self._appid = appid
        self._ws_url = relay_url
        self._reactor = reactor
        self._tor_manager = tor_manager
        self._timing = timing

        self._welcomer = _WelcomeHandler(self._ws_url, __version__,
                                         self._signal_error)
        self._side = bytes_to_hexstr(os.urandom(5))
        self._connection_state = CLOSED
        self._connection_waiters = []
        self._ws_t = None
        self._started_get_code = False
        self._get_code = None
        self._started_input_code = False
        self._input_code_waiter = None
        self._code = None
        self._nameplate_id = None
        self._nameplate_state = CLOSED
        self._mailbox_id = None
        self._mailbox_state = CLOSED
        self._flag_need_nameplate = True
        self._flag_need_to_see_mailbox_used = True
        self._flag_need_to_build_msg1 = True
        self._flag_need_to_send_PAKE = True
        self._establish_key_called = False
        self._key_waiter = None
        self._key = None

        self._version_message = None
        self._version_checked = False
        self._get_verifier_called = False
        self._verifier = None # bytes
        self._verify_result = None # bytes or a Failure
        self._verifier_waiter = None

        self._my_versions = {} # sent
        self._their_versions = {} # received

        self._close_called = False # the close() API has been called
        self._closing = False # we've started shutdown
        self._disconnect_waiter = defer.Deferred()
        self._error = None

        self._next_send_phase = 0
        # send() queues plaintext here, waiting for a connection and the key
        self._plaintext_to_send = [] # (phase, plaintext)
        self._sent_phases = set() # to detect double-send

        self._next_receive_phase = 0
        self._receive_waiters = {} # phase -> Deferred
        self._received_messages = {} # phase -> plaintext

    # API METHODS for applications to call

    # You must use at least one of these entry points, to establish the
    # wormhole code. Other APIs will stall or be queued until we have one.

    # entry point 1: generate a new code. returns a Deferred
    def get_code(self, code_length=2): # XX rename to allocate_code()? create_?
        return self._API_get_code(code_length)

    # entry point 2: interactively type in a code, with completion. returns
    # Deferred
    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        return self._API_input_code(prompt, code_length)

    # entry point 3: paste in a fully-formed code. No return value.
    def set_code(self, code):
        self._API_set_code(code)

    # todo: restore-saved-state entry points

    def establish_key(self):
        """
        returns a Deferred that fires when we've established the shared key.
        When successful, the Deferred fires with a simple `True`, otherwise
        it fails.
        """
        return self._API_establish_key()

    def verify(self):
        """Returns a Deferred that fires when we've heard back from the other
        side, and have confirmed that they used the right wormhole code. When
        successful, the Deferred fires with a "verifier" (a bytestring) which
        can be compared out-of-band before making additional API calls. If
        they used the wrong wormhole code, the Deferred errbacks with
        WrongPasswordError.
        """
        return self._API_verify()

    def send(self, outbound_data):
        return self._API_send(outbound_data)

    def get(self):
        return self._API_get()

    def derive_key(self, purpose, length):
        """Derive a new key from the established wormhole channel for some
        other purpose. This is a deterministic randomized function of the
        session key and the 'purpose' string (unicode/py3-string). This
        cannot be called until verify() or get() has fired.
        """
        return self._API_derive_key(purpose, length)

    def close(self, res=None):
        """Collapse the wormhole, freeing up server resources and flushing
        all pending messages. Returns a Deferred that fires when everything
        is done. It fires with any argument close() was given, to enable use
        as a d.addBoth() handler:

          w = wormhole(...)
          d = w.get()
          ..
          d.addBoth(w.close)
          return d

        Another reasonable approach is to use inlineCallbacks:

          @inlineCallbacks
          def pair(self, code):
              w = wormhole(...)
              try:
                 them = yield w.get()
              finally:
                 yield w.close()
        """
        return self._API_close(res)

    # INTERNAL METHODS beyond here

    def _start(self):
        d = self._connect() # causes stuff to happen
        d.addErrback(log.err)
        return d # fires when connection is established, if you care



    def _make_endpoint(self, hostname, port):
        if self._tor_manager:
            return self._tor_manager.get_endpoint_for(hostname, port)
        # note: HostnameEndpoints have a default 30s timeout
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    def _connect(self):
        # TODO: if we lose the connection, make a new one, re-establish the
        # state
        assert self._side
        self._connection_state = OPENING
        self._ws_t = self._timing.add("open websocket")
        p = urlparse(self._ws_url)
        f = WSFactory(self._ws_url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        f.wormhole = self
        f.d = defer.Deferred()
        # TODO: if hostname="localhost", I get three factories starting
        # and stopping (maybe 127.0.0.1, ::1, and something else?), and
        # an error in the factory is masked.
        ep = self._make_endpoint(p.hostname, p.port or 80)
        # .connect errbacks if the TCP connection fails
        d = ep.connect(f)
        d.addCallback(self._event_connected)
        # f.d is errbacked if WebSocket negotiation fails, and the WebSocket
        # drops any data sent before onOpen() fires, so we must wait for it
        d.addCallback(lambda _: f.d)
        d.addCallback(self._event_ws_opened)
        return d

    def _event_connected(self, ws):
        self._ws = ws
        if self._ws_t:
            self._ws_t.finish()

    def _event_ws_opened(self, _):
        self._connection_state = OPEN
        if self._closing:
            return self._maybe_finished_closing()
        self._ws_send_command("bind", appid=self._appid, side=self._side)
        self._maybe_claim_nameplate()
        self._maybe_send_pake()
        waiters, self._connection_waiters = self._connection_waiters, []
        for d in waiters:
            d.callback(None)

    def _when_connected(self):
        if self._connection_state == OPEN:
            return defer.succeed(None)
        d = defer.Deferred()
        self._connection_waiters.append(d)
        return d

    def _ws_send_command(self, mtype, **kwargs):
        # msgid is used by misc/dump-timing.py to correlate our sends with
        # their receives, and vice versa. They are also correlated with the
        # ACKs we get back from the server (which we otherwise ignore). There
        # are so few messages, 16 bits is enough to be mostly-unique.
        if self.DEBUG: print("SEND", mtype)
        kwargs["id"] = bytes_to_hexstr(os.urandom(2))
        kwargs["type"] = mtype
        payload = dict_to_bytes(kwargs)
        self._timing.add("ws_send", _side=self._side, **kwargs)
        self._ws.sendMessage(payload, False)

    def _ws_dispatch_response(self, payload):
        msg = bytes_to_dict(payload)
        if self.DEBUG and msg["type"]!="ack": print("DIS", msg["type"], msg)
        self._timing.add("ws_receive", _side=self._side, message=msg)
        mtype = msg["type"]
        meth = getattr(self, "_response_handle_"+mtype, None)
        if not meth:
            # make tests fail, but real application will ignore it
            log.err(ValueError("Unknown inbound message type %r" % (msg,)))
            return
        return meth(msg)

    def _response_handle_ack(self, msg):
        pass

    def _response_handle_welcome(self, msg):
        self._welcomer.handle_welcome(msg["welcome"])

    # entry point 1: generate a new code
    @inlineCallbacks
    def _API_get_code(self, code_length):
        if self._code is not None: raise InternalError
        if self._started_get_code: raise InternalError
        self._started_get_code = True
        with self._timing.add("API get_code"):
            yield self._when_connected()
            gc = _GetCode(code_length, self._ws_send_command, self._timing)
            self._get_code = gc
            self._response_handle_allocated = gc._response_handle_allocated
            # TODO: signal_error
            code = yield gc.go()
            self._get_code = None
            self._nameplate_state = OPEN
        self._event_learned_code(code)
        returnValue(code)

    # entry point 2: interactively type in a code, with completion
    @inlineCallbacks
    def _API_input_code(self, prompt, code_length):
        if self._code is not None: raise InternalError
        if self._started_input_code: raise InternalError
        self._started_input_code = True
        with self._timing.add("API input_code"):
            yield self._when_connected()
            ic = _InputCode(self._reactor, prompt, code_length,
                            self._ws_send_command, self._timing)
            self._response_handle_nameplates = ic._response_handle_nameplates
            # we reveal the Deferred we're waiting on, so _signal_error can
            # wake us up if something goes wrong (like a welcome error)
            self._input_code_waiter = ic.go()
            code = yield self._input_code_waiter
            self._input_code_waiter = None
        self._event_learned_code(code)
        returnValue(None)

    # entry point 3: paste in a fully-formed code
    def _API_set_code(self, code):
        self._timing.add("API set_code")
        if not isinstance(code, type(u"")):
            raise TypeError("Unexpected code type '{}'".format(type(code)))
        if self._code is not None:
            raise InternalError
        self._event_learned_code(code)

    # TODO: entry point 4: restore pre-contact saved state (we haven't heard
    # from the peer yet, so we still need the nameplate)

    # TODO: entry point 5: restore post-contact saved state (so we don't need
    # or use the nameplate, only the mailbox)
    def _restore_post_contact_state(self, state):
        # ...
        self._flag_need_nameplate = False
        #self._mailbox_id = X(state)
        self._event_learned_mailbox()

    def _event_learned_code(self, code):
        self._timing.add("code established")
        # bail out early if the password contains spaces...
        # this should raise a useful error
        if ' ' in code:
            raise KeyFormatError("code (%s) contains spaces." % code)
        self._code = code
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        nid = mo.group(1)
        assert isinstance(nid, type("")), type(nid)
        self._nameplate_id = nid
        # fire more events
        self._maybe_build_msg1()
        self._event_learned_nameplate()

    def _maybe_build_msg1(self):
        if not (self._code and self._flag_need_to_build_msg1):
            return
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(to_bytes(self._code),
                                        idSymmetric=to_bytes(self._appid))
            self._msg1 = self._sp.start()
        self._flag_need_to_build_msg1 = False
        self._event_built_msg1()

    def _event_built_msg1(self):
        self._maybe_send_pake()

    # every _maybe_X starts with a set of conditions
    # for each such condition Y, every _event_Y must call _maybe_X

    def _event_learned_nameplate(self):
        self._maybe_claim_nameplate()

    def _maybe_claim_nameplate(self):
        if not (self._nameplate_id and self._connection_state == OPEN):
            return
        self._ws_send_command("claim", nameplate=self._nameplate_id)
        self._nameplate_state = OPEN

    def _response_handle_claimed(self, msg):
        mailbox_id = msg["mailbox"]
        assert isinstance(mailbox_id, type("")), type(mailbox_id)
        self._mailbox_id = mailbox_id
        self._event_learned_mailbox()

    def _event_learned_mailbox(self):
        if not self._mailbox_id: raise InternalError
        assert self._mailbox_state == CLOSED, self._mailbox_state
        if self._closing:
            return
        self._ws_send_command("open", mailbox=self._mailbox_id)
        self._mailbox_state = OPEN
        # causes old messages to be sent now, and subscribes to new messages
        self._maybe_send_pake()
        self._maybe_send_phase_messages()

    def _maybe_send_pake(self):
        # TODO: deal with reentrant call
        if not (self._connection_state == OPEN
                and self._mailbox_state == OPEN
                and self._flag_need_to_send_PAKE):
            return
        body = {"pake_v1": bytes_to_hexstr(self._msg1)}
        payload = dict_to_bytes(body)
        self._msg_send("pake", payload)
        self._flag_need_to_send_PAKE = False

    def _event_received_pake(self, pake_msg):
        payload = bytes_to_dict(pake_msg)
        msg2 = hexstr_to_bytes(payload["pake_v1"])
        with self._timing.add("pake2", waiting="crypto"):
            self._key = self._sp.finish(msg2)
        self._event_established_key()

    def _event_established_key(self):
        self._timing.add("key established")
        self._maybe_notify_key()

        # both sides send different (random) version messages
        self._send_version_message()

        verifier = self._derive_key(b"wormhole:verifier")
        self._event_computed_verifier(verifier)

        self._maybe_check_version()
        self._maybe_send_phase_messages()

    def _API_establish_key(self):
        if self._error: return defer.fail(self._error)
        if self._establish_key_called: raise InternalError
        self._establish_key_called = True
        if self._key is not None:
            return defer.succeed(True)
        self._key_waiter = defer.Deferred()
        return self._key_waiter

    def _maybe_notify_key(self):
        if self._key is None:
            return
        if self._error:
            result = failure.Failure(self._error)
        else:
            result = True
        if self._key_waiter and not self._key_waiter.called:
            self._key_waiter.callback(result)

    def _send_version_message(self):
        # this is encrypted like a normal phase message, and includes a
        # dictionary of version flags to let the other Wormhole know what
        # we're capable of (for future expansion)
        plaintext = dict_to_bytes(self._my_versions)
        phase = "version"
        data_key = self._derive_phase_key(self._side, phase)
        encrypted = self._encrypt_data(data_key, plaintext)
        self._msg_send(phase, encrypted)

    def _API_verify(self):
        if self._error: return defer.fail(self._error)
        if self._get_verifier_called: raise InternalError
        self._get_verifier_called = True
        if self._verify_result:
            return defer.succeed(self._verify_result) # bytes or Failure
        self._verifier_waiter = defer.Deferred()
        return self._verifier_waiter

    def _event_computed_verifier(self, verifier):
        self._verifier = verifier
        self._maybe_notify_verify()

    def _maybe_notify_verify(self):
        if not (self._verifier and self._version_checked):
            return
        if self._error:
            self._verify_result = failure.Failure(self._error)
        else:
            self._verify_result = self._verifier
        if self._verifier_waiter and not self._verifier_waiter.called:
            self._verifier_waiter.callback(self._verify_result)

    def _event_received_version(self, side, body):
        # We ought to have the master key by now, because sensible peers
        # should always send "pake" before sending "version". It might be
        # nice to relax this requirement, which means storing the received
        # version message, and having _event_established_key call
        # _check_version()
        self._version_message = (side, body)
        self._maybe_check_version()

    def _maybe_check_version(self):
        if not (self._key and self._version_message):
            return
        if self._version_checked:
            return
        self._version_checked = True

        side, body = self._version_message
        data_key = self._derive_phase_key(side, "version")
        try:
            plaintext = self._decrypt_data(data_key, body)
        except CryptoError:
            # this makes all API calls fail
            if self.DEBUG: print("CONFIRM FAILED")
            self._signal_error(WrongPasswordError(), "scary")
            return
        msg = bytes_to_dict(plaintext)
        self._version_received(msg)

        self._maybe_notify_verify()

    def _version_received(self, msg):
        self._their_versions = msg

    def _API_send(self, outbound_data):
        if self._error: raise self._error
        if not isinstance(outbound_data, type(b"")):
            raise TypeError(type(outbound_data))
        phase = self._next_send_phase
        self._next_send_phase += 1
        self._plaintext_to_send.append( (phase, outbound_data) )
        with self._timing.add("API send", phase=phase):
            self._maybe_send_phase_messages()

    def _derive_phase_key(self, side, phase):
        assert isinstance(side, type("")), type(side)
        assert isinstance(phase, type("")), type(phase)
        side_bytes = side.encode("ascii")
        phase_bytes = phase.encode("ascii")
        purpose = (b"wormhole:phase:"
                   + sha256(side_bytes).digest()
                   + sha256(phase_bytes).digest())
        return self._derive_key(purpose)

    def _maybe_send_phase_messages(self):
        # TODO: deal with reentrant call
        if not (self._connection_state == OPEN
                and self._mailbox_state == OPEN
                and self._key):
            return
        plaintexts = self._plaintext_to_send
        self._plaintext_to_send = []
        for pm in plaintexts:
            (phase_int, plaintext) = pm
            assert isinstance(phase_int, int), type(phase_int)
            phase = "%d" % phase_int
            data_key = self._derive_phase_key(self._side, phase)
            encrypted = self._encrypt_data(data_key, plaintext)
            self._msg_send(phase, encrypted)

    def _encrypt_data(self, key, data):
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and we automatically ignore
        # reflections.
        # TODO: HKDF(side, nonce, key) ?? include 'side' to prevent
        # reflections, since we no longer compare messages
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(data, type(b"")), type(data)
        assert len(key) == SecretBox.KEY_SIZE, len(key)
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(data, nonce)

    def _msg_send(self, phase, body):
        if phase in self._sent_phases: raise InternalError
        assert self._mailbox_state == OPEN, self._mailbox_state
        self._sent_phases.add(phase)
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        self._timing.add("add", phase=phase)
        self._ws_send_command("add", phase=phase, body=bytes_to_hexstr(body))

    def _event_mailbox_used(self):
        if self.DEBUG: print("_event_mailbox_used")
        if self._flag_need_to_see_mailbox_used:
            self._maybe_release_nameplate()
            self._flag_need_to_see_mailbox_used = False

    def _API_derive_key(self, purpose, length):
        if self._error: raise self._error
        if self._key is None:
            raise InternalError # call derive_key after get_verifier() or get()
        if not isinstance(purpose, type("")): raise TypeError(type(purpose))
        return self._derive_key(to_bytes(purpose), length)

    def _derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(b"")): raise TypeError(type(purpose))
        if self._key is None:
            raise InternalError # call derive_key after get_verifier() or get()
        return HKDF(self._key, length, CTXinfo=purpose)

    def _response_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        assert isinstance(phase, type("")), type(phase)
        body = hexstr_to_bytes(msg["body"])
        if side == self._side:
            return
        self._event_received_peer_message(side, phase, body)

    def _event_received_peer_message(self, side, phase, body):
        # any message in the mailbox means we no longer need the nameplate
        self._event_mailbox_used()

        if self._closing:
            log.msg("received peer message while closing '%s'" % phase)
        if phase in self._received_messages:
            log.msg("ignoring duplicate peer message '%s'" % phase)
            return

        if phase == "pake":
            self._received_messages["pake"] = body
            return self._event_received_pake(body)
        if phase == "version":
            self._received_messages["version"] = body
            return self._event_received_version(side, body)
        if re.search(r'^\d+$', phase):
            return self._event_received_phase_message(side, phase, body)
        # ignore unrecognized phases, for forwards-compatibility
        log.msg("received unknown phase '%s'" % phase)

    def _event_received_phase_message(self, side, phase, body):
        # It's a numbered phase message, aimed at the application above us.
        # Decrypt and deliver upstairs, notifying anyone waiting on it
        try:
            data_key = self._derive_phase_key(side, phase)
            plaintext = self._decrypt_data(data_key, body)
        except CryptoError:
            e = WrongPasswordError()
            self._signal_error(e, "scary") # flunk all other API calls
            # make tests fail, if they aren't explicitly catching it
            if self.DEBUG: print("CryptoError in msg received")
            log.err(e)
            if self.DEBUG: print(" did log.err", e)
            return # ignore this message
        self._received_messages[phase] = plaintext
        if phase in self._receive_waiters:
            d = self._receive_waiters.pop(phase)
            d.callback(plaintext)

    def _decrypt_data(self, key, encrypted):
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(encrypted, type(b"")), type(encrypted)
        assert len(key) == SecretBox.KEY_SIZE, len(key)
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data

    def _API_get(self):
        if self._error: return defer.fail(self._error)
        phase = "%d" % self._next_receive_phase
        self._next_receive_phase += 1
        with self._timing.add("API get", phase=phase):
            if phase in self._received_messages:
                return defer.succeed(self._received_messages[phase])
            d = self._receive_waiters[phase] = defer.Deferred()
            return d

    def _signal_error(self, error, mood):
        if self.DEBUG: print("_signal_error", error, mood)
        if self._error:
            return
        self._maybe_close(error, mood)
        if self.DEBUG: print("_signal_error done")

    @inlineCallbacks
    def _API_close(self, res, mood="happy"):
        if self.DEBUG: print("close")
        if self._close_called: raise InternalError
        self._close_called = True
        self._maybe_close(WormholeClosedError(), mood)
        if self.DEBUG: print("waiting for disconnect")
        yield self._disconnect_waiter
        returnValue(res)

    def _maybe_close(self, error, mood):
        if self._closing:
            return

        # ordering constraints:
        # * must wait for nameplate/mailbox acks before closing the websocket
        # * must mark APIs for failure before errbacking Deferreds
        #   * since we give up control
        # * must mark self._closing before errbacking Deferreds
        #   * since caller may call close() when we give up control
        #   * and close() will reenter _maybe_close

        self._error = error # causes new API calls to fail

        # since we're about to give up control by errbacking any API
        # Deferreds, set self._closing, to make sure that a new call to
        # close() isn't going to confuse anything
        self._closing = True

        # now errback all API deferreds except close(): get_code,
        # input_code, verify, get
        if self._input_code_waiter and not self._input_code_waiter.called:
            self._input_code_waiter.errback(error)
        for d in self._connection_waiters: # input_code, get_code (early)
            if self.DEBUG: print("EB cw")
            d.errback(error)
        if self._get_code: # get_code (late)
            if self.DEBUG: print("EB gc")
            self._get_code._allocated_d.errback(error)
        if self._verifier_waiter and not self._verifier_waiter.called:
            if self.DEBUG: print("EB VW")
            self._verifier_waiter.errback(error)
        if self._key_waiter and not self._key_waiter.called:
            if self.DEBUG: print("EB KW")
            self._key_waiter.errback(error)
        for d in self._receive_waiters.values():
            if self.DEBUG: print("EB RW")
            d.errback(error)
        # Release nameplate and close mailbox, if either was claimed/open.
        # Since _closing is True when both ACKs come back, the handlers will
        # close the websocket. When *that* finishes, _disconnect_waiter()
        # will fire.
        self._maybe_release_nameplate()
        self._maybe_close_mailbox(mood)
        # In the off chance we got closed before we even claimed the
        # nameplate, give _maybe_finished_closing a chance to run now.
        self._maybe_finished_closing()

    def _maybe_release_nameplate(self):
        if self.DEBUG: print("_maybe_release_nameplate", self._nameplate_state)
        if self._nameplate_state == OPEN:
            if self.DEBUG: print(" sending release")
            self._ws_send_command("release")
            self._nameplate_state = CLOSING

    def _response_handle_released(self, msg):
        self._nameplate_state = CLOSED
        self._maybe_finished_closing()

    def _maybe_close_mailbox(self, mood):
        if self.DEBUG: print("_maybe_close_mailbox", self._mailbox_state)
        if self._mailbox_state == OPEN:
            if self.DEBUG: print(" sending close")
            self._ws_send_command("close", mood=mood)
            self._mailbox_state = CLOSING

    def _response_handle_closed(self, msg):
        self._mailbox_state = CLOSED
        self._maybe_finished_closing()

    def _maybe_finished_closing(self):
        if self.DEBUG: print("_maybe_finished_closing", self._closing, self._nameplate_state, self._mailbox_state, self._connection_state)
        if not self._closing:
            return
        if (self._nameplate_state == CLOSED
            and self._mailbox_state == CLOSED
            and self._connection_state == OPEN):
            self._connection_state = CLOSING
            self._drop_connection()

    def _drop_connection(self):
        # separate method so it can be overridden by tests
        self._ws.transport.loseConnection() # probably flushes output
        # calls _ws_closed() when done

    def _ws_closed(self, wasClean, code, reason):
        # For now (until we add reconnection), losing the websocket means
        # losing everything. Make all API callers fail. Help someone waiting
        # in close() to finish
        self._connection_state = CLOSED
        self._disconnect_waiter.callback(None)
        self._maybe_finished_closing()

        # what needs to happen when _ws_closed() happens unexpectedly
        # * errback all API deferreds
        # * maybe: cause new API calls to fail
        # * obviously can't release nameplate or close mailbox
        # * can't re-close websocket
        # * close(wait=True) callers should fire right away

def wormhole(appid, relay_url, reactor, tor_manager=None, timing=None):
    timing = timing or DebugTiming()
    w = _Wormhole(appid, relay_url, reactor, tor_manager, timing)
    w._start()
    return w

#def wormhole_from_serialized(data, reactor, timing=None):
#    timing = timing or DebugTiming()
#    w = _Wormhole.from_serialized(data, reactor, timing)
#    return w
