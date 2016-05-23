from __future__ import print_function, absolute_import
import os, sys, json, re, unicodedata
from six.moves.urllib_parse import urlparse
from binascii import hexlify, unhexlify
from twisted.internet import defer, endpoints, error
from twisted.internet.threads import deferToThread, blockingCallFromThread
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log
from autobahn.twisted import websocket
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from spake2 import SPAKE2_Symmetric
from . import __version__
from . import codes
#from .errors import ServerError, Timeout
from .errors import WrongPasswordError, UsageError
from .timing import DebugTiming
from hkdf import Hkdf

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

CONFMSG_NONCE_LENGTH = 128//8
CONFMSG_MAC_LENGTH = 256//8
def make_confmsg(confkey, nonce):
    return nonce+HKDF(confkey, CONFMSG_MAC_LENGTH, nonce)

def to_bytes(u):
    return unicodedata.normalize("NFC", u).encode("utf-8")

# We send the following messages through the relay server to the far side (by
# sending "add" commands to the server, and getting "message" responses):
#
# phase=setup:
#   * unauthenticated version strings (but why?)
#   * early warmup for connection hints ("I can do tor, spin up HS")
#   * wordlist l10n identifier
# phase=pake: just the SPAKE2 'start' message (binary)
# phase=confirm: key verification (HKDF(key, nonce)+nonce)
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
            self._send_command(u"allocate")
            nameplate_id = yield self._allocated_d
        code = codes.make_code(nameplate_id, self._code_length)
        assert isinstance(code, type(u"")), type(code)
        returnValue(code)

    def _response_handle_allocated(self, msg):
        nid = msg["nameplate"]
        assert isinstance(nid, type(u"")), type(nid)
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
        self._send_command(u"list")
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
            nameplate_id = n[u"id"]
            assert isinstance(nameplate_id, type(u"")), type(nameplate_id)
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
        self._motd_displayed = False
        self._current_version = current_version
        self._signal_error = signal_error

    def handle_welcome(self, welcome):
        if ("motd" in welcome and
            not self._motd_displayed):
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" %
                  (self._ws_url, motd_formatted), file=sys.stderr)
            self._motd_displayed = True

        # Only warn if we're running a release version (e.g. 0.0.6, not
        # 0.0.6-DISTANCE-gHASH). Only warn once.
        if ("current_version" in welcome
            and "-" not in self._current_version
            and not self._version_warning_displayed
            and welcome["current_version"] != self._current_version):
            print("Warning: errors may occur unless both sides are running the same version", file=sys.stderr)
            print("Server claims %s is current, but ours is %s"
                  % (welcome["current_version"], self._current_version),
                  file=sys.stderr)
            self._version_warning_displayed = True

        if "error" in welcome:
            return self._signal_error(welcome["error"])


class _Wormhole:
    def __init__(self, appid, relay_url, reactor, tor_manager, timing):
        self._appid = appid
        self._ws_url = relay_url
        self._reactor = reactor
        self._tor_manager = tor_manager
        self._timing = timing

        self._welcomer = _WelcomeHandler(self._ws_url, __version__,
                                         self._signal_error)
        self._side = hexlify(os.urandom(5)).decode("ascii")
        self._connected = None
        self._connection_waiters = []
        self._started_get_code = False
        self._code = None
        self._nameplate_id = None
        self._nameplate_claimed = False
        self._nameplate_released = False
        self._release_waiter = defer.Deferred()
        self._mailbox_id = None
        self._mailbox_opened = False
        self._mailbox_closed = False
        self._close_waiter = defer.Deferred()
        self._flag_need_nameplate = True
        self._flag_need_to_see_mailbox_used = True
        self._flag_need_to_build_msg1 = True
        self._flag_need_to_send_PAKE = True
        self._key = None
        self._closed = False
        self._mood = u"happy"

        self._get_verifier_called = False
        self._verifier_waiter = defer.Deferred()

        self._next_send_phase = 0
        # send() queues plaintext here, waiting for a connection and the key
        self._plaintext_to_send = [] # (phase, plaintext)
        self._sent_phases = set() # to detect double-send

        self._next_receive_phase = 0
        self._receive_waiters = {} # phase -> Deferred
        self._received_messages = {} # phase -> plaintext

    def _signal_error(self, error):
        # close the mailbox with an "errory" mood, errback all Deferreds,
        # record the error, fail all subsequent API calls
        pass # XXX

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
        p = urlparse(self._ws_url)
        f = WSFactory(self._ws_url)
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
        self._ws_t = self._timing.add("websocket")

    def _event_ws_opened(self, _):
        self._connected = True
        self._ws_send_command(u"bind", appid=self._appid, side=self._side)
        self._maybe_get_mailbox()
        self._maybe_send_pake()
        waiters, self._connection_waiters = self._connection_waiters, []
        for d in waiters:
            d.callback(None)

    def _when_connected(self):
        if self._connected:
            return defer.succeed(None)
        d = defer.Deferred()
        self._connection_waiters.append(d)
        return d

    def _ws_send_command(self, mtype, **kwargs):
        # msgid is used by misc/dump-timing.py to correlate our sends with
        # their receives, and vice versa. They are also correlated with the
        # ACKs we get back from the server (which we otherwise ignore). There
        # are so few messages, 16 bits is enough to be mostly-unique.
        kwargs["id"] = hexlify(os.urandom(2)).decode("ascii")
        kwargs["type"] = mtype
        payload = json.dumps(kwargs).encode("utf-8")
        self._timing.add("ws_send", _side=self._side, **kwargs)
        self._ws.sendMessage(payload, False)

    DEBUG=False
    def _ws_dispatch_response(self, payload):
        msg = json.loads(payload.decode("utf-8"))
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
    def get_code(self, code_length=2): # XX rename to allocate_code()? create_?
        if self._code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        with self._timing.add("API get_code"):
            yield self._when_connected()
            gc = _GetCode(code_length, self._ws_send_command, self._timing)
            self._response_handle_allocated = gc._response_handle_allocated
            code = yield gc.go()
            self._nameplate_claimed = True # side-effect of allocation
        self._event_learned_code(code)
        returnValue(code)

    # entry point 2: interactively type in a code, with completion
    @inlineCallbacks
    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        if self._code is not None: raise UsageError
        if self._started_input_code: raise UsageError
        self._started_input_code = True
        with self._timing.add("API input_code"):
            yield self._when_connected()
            ic = _InputCode(prompt, code_length, self._ws_send_command)
            self._response_handle_nameplates = ic._response_handle_nameplates
            code = yield ic.go()
        self._event_learned_code(code)
        returnValue(None)

    # entry point 3: paste in a fully-formed code
    def set_code(self, code):
        self._timing.add("API set_code")
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self._code is not None: raise UsageError
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
        self._code = code
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        nid = mo.group(1)
        assert isinstance(nid, type(u"")), type(nid)
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
        self._maybe_get_mailbox()

    def _maybe_get_mailbox(self):
        if not (self._nameplate_id and self._connected):
            return
        self._ws_send_command(u"claim", nameplate=self._nameplate_id)
        self._nameplate_claimed = True

    def _response_handle_claimed(self, msg):
        mailbox_id = msg["mailbox"]
        assert isinstance(mailbox_id, type(u"")), type(mailbox_id)
        self._mailbox_id = mailbox_id
        self._event_learned_mailbox()

    def _event_learned_mailbox(self):
        if not self._mailbox_id: raise UsageError
        if self._mailbox_opened: raise UsageError
        self._ws_send_command(u"open", mailbox=self._mailbox_id)
        self._mailbox_opened = True
        # causes old messages to be sent now, and subscribes to new messages
        self._maybe_send_pake()
        self._maybe_send_phase_messages()

    def _maybe_send_pake(self):
        # TODO: deal with reentrant call
        if not (self._connected and self._mailbox_opened
                and self._flag_need_to_send_PAKE):
            return
        self._msg_send(u"pake", self._msg1)
        self._flag_need_to_send_PAKE = False

    def _event_received_pake(self, pake_msg):
        with self._timing.add("pake2", waiting="crypto"):
            self._key = self._sp.finish(pake_msg)
        self._event_established_key()

    def _event_established_key(self):
        self._timing.add("key established")

        # both sides send different (random) confirmation messages
        confkey = self.derive_key(u"wormhole:confirmation")
        nonce = os.urandom(CONFMSG_NONCE_LENGTH)
        confmsg = make_confmsg(confkey, nonce)
        self._msg_send(u"confirm", confmsg)

        verifier = self.derive_key(u"wormhole:verifier")
        self._event_computed_verifier(verifier)

        self._maybe_send_phase_messages()

    def get_verifier(self):
        if self._closed: raise UsageError
        if self._get_verifier_called: raise UsageError
        self._get_verifier_called = True
        return self._verifier_waiter

    def _event_computed_verifier(self, verifier):
        self._verifier_waiter.callback(verifier)

    def _event_received_confirm(self, body):
        # TODO: we might not have a master key yet, if the caller wasn't
        # waiting in _get_master_key() when a back-to-back pake+_confirm
        # message pair arrived.
        confkey = self.derive_key(u"wormhole:confirmation")
        nonce = body[:CONFMSG_NONCE_LENGTH]
        if body != make_confmsg(confkey, nonce):
            # this makes all API calls fail
            return self._signal_error(WrongPasswordError())


    def send(self, outbound_data):
        if not isinstance(outbound_data, type(b"")):
            raise TypeError(type(outbound_data))
        if self._closed: raise UsageError
        phase = self._next_send_phase
        self._next_send_phase += 1
        self._plaintext_to_send.append( (phase, outbound_data) )
        with self._timing.add("API send", phase=phase):
            self._maybe_send_phase_messages()

    def _maybe_send_phase_messages(self):
        # TODO: deal with reentrant call
        if not (self._connected and self._mailbox_opened and self._key):
            return
        plaintexts = self._plaintext_to_send
        self._plaintext_to_send = []
        for pm in plaintexts:
            (phase, plaintext) = pm
            assert isinstance(phase, int), type(phase)
            data_key = self.derive_key(u"wormhole:phase:%d" % phase)
            encrypted = self._encrypt_data(data_key, plaintext)
            self._msg_send(u"%d" % phase, encrypted)

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
        if phase in self._sent_phases: raise UsageError
        if not self._mailbox_opened: raise UsageError
        if self._mailbox_closed: raise UsageError
        self._sent_phases.add(phase)
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        self._timing.add("add", phase=phase)
        self._ws_send_command(u"add", phase=phase,
                              body=hexlify(body).decode("ascii"))


    def _event_mailbox_used(self):
        if self.DEBUG: print("_event_mailbox_used")
        if self._flag_need_to_see_mailbox_used:
            self._maybe_release_nameplate()
            self._flag_need_to_see_mailbox_used = False

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(u"")): raise TypeError(type(purpose))
        if self._key is None:
            raise UsageError # call derive_key after get_verifier() or get()
        return HKDF(self._key, length, CTXinfo=to_bytes(purpose))

    def _response_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        assert isinstance(phase, type(u"")), type(phase)
        body = unhexlify(msg["body"].encode("ascii"))
        if side == self._side:
            return
        self._event_received_peer_message(phase, body)

    def _event_received_peer_message(self, phase, body):
        # any message in the mailbox means we no longer need the nameplate
        self._event_mailbox_used()
        #if phase in self._received_messages:
        #    # a nameplate collision would cause this
        #    err = ServerError("got duplicate phase %s" % phase, self._ws_url)
        #    return self._signal_error(err)
        #self._received_messages[phase] = body
        if phase == u"pake":
            self._event_received_pake(body)
            return
        if phase == u"confirm":
            self._event_received_confirm(body)
            return

        # now notify anyone waiting on it
        try:
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            plaintext = self._decrypt_data(data_key, body)
        except CryptoError:
            raise WrongPasswordError # TODO: signal
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

    def get(self):
        if self._closed: raise UsageError
        phase = u"%d" % self._next_receive_phase
        self._next_receive_phase += 1
        with self._timing.add("API get", phase=phase):
            if phase in self._received_messages:
                return defer.succeed(self._received_messages[phase])
            d = self._receive_waiters[phase] = defer.Deferred()
            return d

    @inlineCallbacks
    def close(self, mood=None, wait=False):
        # TODO: auto-close on error, mostly for load-from-state
        if self._closed: raise UsageError
        if mood:
            self._mood = mood
        self._maybe_release_nameplate()
        self._maybe_close_mailbox()
        if wait:
            if self._nameplate_claimed:
                yield self._release_waiter
            if self._mailbox_opened:
                yield self._close_waiter
        self._drop_connection()

    def _maybe_release_nameplate(self):
        if self.DEBUG: print("_maybe_release_nameplate", self._nameplate_claimed, self._nameplate_released)
        if self._nameplate_claimed and not self._nameplate_released:
            if self.DEBUG: print(" sending release")
            self._ws_send_command(u"release")
            self._nameplate_released = True

    def _response_handle_released(self, msg):
        self._release_waiter.callback(None)

    def _maybe_close_mailbox(self):
        if self._mailbox_opened and not self._mailbox_closed:
            self._ws_send_command(u"close", mood=self._mood)
            self._mailbox_closed = True

    def _response_handle_closed(self, msg):
        self._close_waiter.callback(None)

    def _drop_connection(self):
        self._ws.transport.loseConnection() # probably flushes
        # calls _ws_closed() when done

    def _ws_closed(self, wasClean, code, reason):
        pass

def wormhole(appid, relay_url, reactor, tor_manager=None, timing=None):
    timing = timing or DebugTiming()
    w = _Wormhole(appid, relay_url, reactor, tor_manager, timing)
    w._start()
    return w

def wormhole_from_serialized(data, reactor, timing=None):
    timing = timing or DebugTiming()
    w = _Wormhole.from_serialized(data, reactor, timing)
    return w
