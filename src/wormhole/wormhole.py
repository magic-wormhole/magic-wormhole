from __future__ import print_function
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
from .. import __version__
from .. import codes
from ..errors import ServerError, Timeout, WrongPasswordError, UsageError
from ..timing import DebugTiming
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
    def __init__(self, code_length, send_command):
        self._code_length = code_length
        self._send_command = send_command
        self._allocated_d = defer.Deferred()

    @inlineCallbacks
    def go(self):
        with self._timing.add("allocate"):
            self._send_command(u"allocate")
            nameplate_id = yield self._allocated_d
        code = codes.make_code(nameplate_id, self._code_length)
        assert isinstance(code, type(u"")), type(code)
        returnValue(code)

    def _ws_handle_allocated(self, msg):
        nid = msg["nameplate"]
        assert isinstance(nid, type(u"")), type(nid)
        self._allocated_d.callback(nid)

class _InputCode:
    def __init__(self, reactor, prompt, code_length, send_command):
        self._reactor = reactor
        self._prompt = prompt
        self._code_length = code_length
        self._send_command = send_command

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

    def _ws_handle_nameplates(self, msg):
        nameplates = msg["nameplates"]
        assert isinstance(nameplates, list), type(nameplates)
        for nameplate_id in nameplates:
            assert isinstance(nameplate_id, type(u"")), type(nameplate_id)
        self._lister_d.callback(nameplates)

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



class _Wormhole:
    def __init__(self):
        self._connected = None
        self._flag_need_mailbox = True
        self._flag_need_to_see_mailbox_used = True
        self._flag_need_to_build_msg1 = True
        self._flag_need_to_send_PAKE = True
        self._flag_need_PAKE = True
        self._flag_need_key = True # rename to not self._key

        self._next_send_phase = 0
        self._phase_messages_to_send = [] # not yet acked by server

        self._next_receive_phase = 0
        self._phase_messages_received = {} # phase -> message


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
        d.addCallback(self._event_ws_opened)
        return d

    def _event_connected(self, ws, f):
        self._ws = ws
        self._ws_t = self._timing.add("websocket")

    def _event_ws_opened(self, _):
        self._connected = True
        self._ws_send_command(u"bind", appid=self._appid, side=self._side)
        self._maybe_get_mailbox()

    def _ws_handle_welcome(self, msg):
        welcome = msg["welcome"]
        if ("motd" in welcome and
            not self.motd_displayed):
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" %
                  (self._ws_url, motd_formatted), file=sys.stderr)
            self.motd_displayed = True

        # Only warn if we're running a release version (e.g. 0.0.6, not
        # 0.0.6-DISTANCE-gHASH). Only warn once.
        if ("-" not in __version__ and
            not self.version_warning_displayed and
            welcome["current_version"] != __version__):
            print("Warning: errors may occur unless both sides are running the same version", file=sys.stderr)
            print("Server claims %s is current, but ours is %s"
                  % (welcome["current_version"], __version__), file=sys.stderr)
            self.version_warning_displayed = True

        if "error" in welcome:
            return self._signal_error(welcome["error"])


    # entry point 1: generate a new code
    @inlineCallbacks
    def get_code(self, code_length=2): # XX rename to allocate_code()? create_?
        if self._code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        with self._timing.add("API get_code"):
            gc = _GetCode(code_length, self._ws_send_command)
            self._ws_handle_allocated = gc._ws_handle_allocated
            code = yield gc.go()
        self._event_learned_code(code)
        returnValue(code)

    # entry point 2: interactively type in a code, with completion
    @inlineCallbacks
    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        if self._code is not None: raise UsageError
        if self._started_input_code: raise UsageError
        self._started_input_code = True
        with self._timing.add("API input_code"):
            gc = _InputCode(prompt, code_length, self._ws_send_command)
            self._ws_handle_nameplates = gc._ws_handle_nameplates
            code = yield gc.go()
        self._event_learned_code(code)
        returnValue(None)

    # entry point 3: paste in a fully-formed code
    def set_code(self, code):
        self._timing.add("API set_code")
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self._code is not None: raise UsageError
        self._event_learned_code(code)

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
        if not (self._flag_need_mailbox and self._nameplate_id
                and self._connected):
            return
        self._ws_send_command(u"claim", nameplate=self._nameplate_id)

    def _ws_handle_claimed(self, msg):
        mailbox_id = msg["mailbox"]
        assert isinstance(mailbox_id, type(u"")), type(mailbox_id)
        self._mailbox_id = mailbox_id
        self._event_learned_mailbox()

    def _event_welcome(self):
        pass

    def _event_learned_mailbox(self):
        self._flag_need_mailbox = False
        if not self._mailbox_id: raise UsageError
        if self._mailbox_opened: raise UsageError
        self._ws_send_command(u"open", mailbox=self._mailbox_id)
        # causes old messages to be sent now, and subscribes to new messages
        self._maybe_send_pake()
        self._maybe_send_phase_messages()

    def _maybe_send_pake(self):
        # TODO: deal with reentrant call
        if not (self._connected and self._mailbox
                and self._flag_need_to_send_PAKE):
            return
        d = self._msg_send(u"pake", self._msg1)
        def _pake_sent(res):
            self._flag_need_to_send_PAKE = False
        d.addCallback(_pake_sent)
        d.addErrback(log.err)

    def _maybe_send_phase_messages(self):
        # TODO: deal with reentrant call
        if not (self._connected and self._mailbox and self._key):
            return
        for pm in self._phase_messages_to_send:
            (phase, message) = pm
            d = self._msg_send(phase, message)
            def _phase_message_sent(res, pm=pm):
                try:
                    self._phase_messages_to_send.remove(pm)
                except ValueError:
                    pass
            d.addCallback(_phase_message_sent)
            d.addErrback(log.err)



    def _event_received_message(self, msg):
        pass
    def _event_mailbox_used(self):
        if self._flag_need_to_see_mailbox_used:
            self._ws_send_command(u"release")
            self._flag_need_to_see_mailbox_used = False

    def _event_learned_PAKE(self, pake_msg):
        with self._timing.add("pake2", waiting="crypto"):
            self._key = self._sp.finish(pake_msg)
        self._event_established_key()

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(u"")): raise TypeError(type(purpose))
        if self._key is None:
            # call after get_verifier() or get()
            raise UsageError
        return HKDF(self._key, length, CTXinfo=to_bytes(purpose))

    def _event_established_key(self):
        self._timing.add("key established")
        if self._send_confirm:
            # both sides send different (random) confirmation messages
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = os.urandom(CONFMSG_NONCE_LENGTH)
            confmsg = make_confmsg(confkey, nonce)
            self._msg_send(u"confirm", confmsg, wait=True)
        verifier = self.derive_key(u"wormhole:verifier")
        self._event_computed_verifier(verifier)
        pass
    def _event_computed_verifier(self, verifier):
        self._verifier = verifier
        d, self._verifier_waiter = self._verifier_waiter, None
        if d:
            d.callback(verifier)

    def _event_received_confirm(self, body):
        # TODO: we might not have a master key yet, if the caller wasn't
        # waiting in _get_master_key() when a back-to-back pake+_confirm
        # message pair arrived.
        confkey = self.derive_key(u"wormhole:confirmation")
        nonce = body[:CONFMSG_NONCE_LENGTH]
        if body != make_confmsg(confkey, nonce):
            # this makes all API calls fail
            return self._signal_error(WrongPasswordError())

    def _event_received_phase_message(self, phase, message):
        self._phase_messages_received[phase] = message
        if phase in self._phase_message_waiters:
            d = self._phase_message_waiters.pop(phase)
            d.callback(message)

    def _ws_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        body = unhexlify(msg["body"].encode("ascii"))
        if side == self._side:
            return
        self._event_received_peer_message(phase, body)

    def XXXackstuff():
        if phase in self._sent_messages and self._sent_messages[phase] == body:
            self._delivered_messages.add(phase) # ack by server
            self._wakeup()
            return # ignore echoes of our outbound messages
        
    def _event_received_peer_message(self, phase, body):
        # any message in the mailbox means we no longer need the nameplate
        self._event_mailbox_used()
        #if phase in self._received_messages:
        #    # a nameplate collision would cause this
        #    err = ServerError("got duplicate phase %s" % phase, self._ws_url)
        #    return self._signal_error(err)
        #self._received_messages[phase] = body
        if phase == u"confirm":
            self._event_received_confirm(body)
        # now notify anyone waiting on it
        self._wakeup()

    def _event_asked_to_send_phase_message(self, phase, message):
        pm = (phase, message)
        self._phase_messages_to_send.append(pm)
        self._maybe_send_phase_messages()

    def _event_asked_to_close(self):
        pass
    


def wormhole(appid, relay_url, reactor, tor_manager=None, timing=None):
    w = _Wormhole(appid, relay_url, reactor, tor_manager, timing)
    w._start()
    return w

def wormhole_from_serialized(data, reactor):
    w = _Wormhole.from_serialized(data, reactor)
    return w
