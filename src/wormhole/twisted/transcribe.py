from __future__ import print_function
import os, sys, json, re, unicodedata
from six.moves.urllib_parse import urlparse
from binascii import hexlify, unhexlify
from twisted.internet import reactor, defer, endpoints, error
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

class WSClient(websocket.WebSocketClientProtocol):
    def onOpen(self):
        self.wormhole_open = True
        self.factory.d.callback(self)

    def onMessage(self, payload, isBinary):
        assert not isBinary
        self.wormhole._ws_dispatch_msg(payload)

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

class Wormhole:
    motd_displayed = False
    version_warning_displayed = False
    _send_confirm = True

    def __init__(self, appid, relay_url, tor_manager=None, timing=None,
                 reactor=reactor):
        if not isinstance(appid, type(u"")): raise TypeError(type(appid))
        if not isinstance(relay_url, type(u"")):
            raise TypeError(type(relay_url))
        if not relay_url.endswith(u"/"): raise UsageError
        self._appid = appid
        self._relay_url = relay_url
        self._ws_url = relay_url.replace("http:", "ws:") + "ws"
        self._tor_manager = tor_manager
        self._timing = timing or DebugTiming()
        self._reactor = reactor
        self._side = hexlify(os.urandom(5)).decode("ascii")
        self._code = None
        self._channelid = None
        self._key = None
        self._started_get_code = False
        self._sent_messages = set() # (phase, body_bytes)
        self._delivered_messages = set() # (phase, body_bytes)
        self._received_messages = {} # phase -> body_bytes
        self._sent_phases = set() # phases, to prohibit double-send
        self._got_phases = set() # phases, to prohibit double-read
        self._sleepers = []
        self._confirmation_failed = False
        self._closed = False
        self._deallocated_status = None
        self._timing_started = self._timing.add("wormhole")
        self._ws = None
        self._ws_t = None # timing Event
        self._ws_channel_claimed = False
        self._error = None

    def _make_endpoint(self, hostname, port):
        if self._tor_manager:
            return self._tor_manager.get_endpoint_for(hostname, port)
        # note: HostnameEndpoints have a default 30s timeout
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    @inlineCallbacks
    def _get_websocket(self):
        if not self._ws:
            # TODO: if we lose the connection, make a new one
            #from twisted.python import log
            #log.startLogging(sys.stderr)
            assert self._side
            assert not self._ws_channel_claimed
            p = urlparse(self._ws_url)
            f = WSFactory(self._ws_url)
            f.wormhole = self
            f.d = defer.Deferred()
            # TODO: if hostname="localhost", I get three factories starting
            # and stopping (maybe 127.0.0.1, ::1, and something else?), and
            # an error in the factory is masked.
            ep = self._make_endpoint(p.hostname, p.port or 80)
            # .connect errbacks if the TCP connection fails
            self._ws = yield ep.connect(f)
            self._ws_t = self._timing.add("websocket")
            # f.d is errbacked if WebSocket negotiation fails
            yield f.d # WebSocket drops data sent before onOpen() fires
            self._ws_send(u"bind", appid=self._appid, side=self._side)
        # the socket is connected, and bound, but no channel has been claimed
        returnValue(self._ws)

    @inlineCallbacks
    def _ws_send(self, mtype, **kwargs):
        ws = yield self._get_websocket()
        # msgid is used by misc/dump-timing.py to correlate our sends with
        # their receives, and vice versa. They are also correlated with the
        # ACKs we get back from the server (which we otherwise ignore). There
        # are so few messages, 16 bits is enough to be mostly-unique.
        kwargs["id"] = hexlify(os.urandom(2)).decode("ascii")
        kwargs["type"] = mtype
        payload = json.dumps(kwargs).encode("utf-8")
        self._timing.add("ws_send", _side=self._side, **kwargs)
        ws.sendMessage(payload, False)

    def _ws_dispatch_msg(self, payload):
        msg = json.loads(payload.decode("utf-8"))
        self._timing.add("ws_receive", _side=self._side, message=msg)
        mtype = msg["type"]
        meth = getattr(self, "_ws_handle_"+mtype, None)
        if not meth:
            # make tests fail, but real application will ignore it
            log.err(ValueError("Unknown inbound message type %r" % (msg,)))
            return
        return meth(msg)

    def _ws_handle_ack(self, msg):
        pass

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

    @inlineCallbacks
    def _sleep(self, wake_on_error=True):
        if wake_on_error and self._error:
            # don't sleep if the bed's already on fire, unless we're waiting
            # for the fire department to respond, in which case sure, keep on
            # sleeping
            raise self._error
        d = defer.Deferred()
        self._sleepers.append(d)
        yield d
        if wake_on_error and self._error:
            raise self._error

    def _wakeup(self):
        sleepers = self._sleepers
        self._sleepers = []
        for d in sleepers:
            d.callback(None)
            # NOTE: callers should avoid reentrancy themselves. An
            # eventual-send would be safer here, but it makes synchronizing
            # unit tests annoying.

    def _signal_error(self, error):
        assert isinstance(error, Exception)
        self._error = error
        self._wakeup()

    def _ws_handle_error(self, msg):
        err = ServerError("%s: %s" % (msg["error"], msg["orig"]),
                          self._ws_url)
        return self._signal_error(err)

    @inlineCallbacks
    def _claim_channel_and_watch(self):
        assert self._channelid is not None
        yield self._get_websocket()
        if not self._ws_channel_claimed:
            yield self._ws_send(u"claim", channelid=self._channelid)
            self._ws_channel_claimed = True
            yield self._ws_send(u"watch")

    # entry point 1: generate a new code
    @inlineCallbacks
    def get_code(self, code_length=2): # rename to allocate_code()? create_?
        if self._code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        with self._timing.add("API get_code"):
            with self._timing.add("allocate"):
                yield self._ws_send(u"allocate")
                while self._channelid is None:
                    yield self._sleep()
            code = codes.make_code(self._channelid, code_length)
            assert isinstance(code, type(u"")), type(code)
            self._set_code(code)
            self._start()
        returnValue(code)

    def _ws_handle_allocated(self, msg):
        if self._channelid is not None:
            return self._signal_error("got duplicate channelid")
        self._channelid = msg["channelid"]
        self._wakeup()

    def _start(self):
        # allocate the rest now too, so it can be serialized
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(to_bytes(self._code),
                                        idSymmetric=to_bytes(self._appid))
            self._msg1 = self._sp.start()

    # entry point 2a: interactively type in a code, with completion
    @inlineCallbacks
    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        def _lister():
            return blockingCallFromThread(self._reactor, self._list_channels)
        # fetch the list of channels ahead of time, to give us a chance to
        # discover the welcome message (and warn the user about an obsolete
        # client)
        #
        # TODO: send the request early, show the prompt right away, hide the
        # latency in the user's indecision and slow typing. If we're lucky
        # the answer will come back before they hit TAB.
        with self._timing.add("API input_code"):
            initial_channelids = yield self._list_channels()
            with self._timing.add("input code", waiting="user"):
                t = self._reactor.addSystemEventTrigger("before", "shutdown",
                                                        self._warn_readline)
                code = yield deferToThread(codes.input_code_with_completion,
                                           prompt,
                                           initial_channelids, _lister,
                                           code_length)
                self._reactor.removeSystemEventTrigger(t)
        returnValue(code) # application will give this to set_code()

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
        #   us to fetch the current channelid list.
        #
        # Note that hard-terminating our process with os.kill(os.getpid(),
        # signal.SIGKILL), or SIGTERM, doesn't seem to work: the thread
        # doesn't see the signal, and we must still wait for stdin to make
        # readline finish.

    @inlineCallbacks
    def _list_channels(self):
        with self._timing.add("list"):
            self._latest_channelids = None
            yield self._ws_send(u"list")
            while self._latest_channelids is None:
                yield self._sleep()
        returnValue(self._latest_channelids)

    def _ws_handle_channelids(self, msg):
        self._latest_channelids = msg["channelids"]
        self._wakeup()

    # entry point 2b: paste in a fully-formed code
    def set_code(self, code):
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self._code is not None: raise UsageError
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        with self._timing.add("API set_code"):
            self._channelid = int(mo.group(1))
            self._set_code(code)
            self._start()

    def _set_code(self, code):
        if self._code is not None: raise UsageError
        self._timing.add("code established")
        self._code = code

    def serialize(self):
        # I can only be serialized after get_code/set_code and before
        # get_verifier/get_data
        if self._code is None: raise UsageError
        if self._key is not None: raise UsageError
        if self._sent_phases: raise UsageError
        if self._got_phases: raise UsageError
        data = {
            "appid": self._appid,
            "relay_url": self._relay_url,
            "code": self._code,
            "channelid": self._channelid,
            "side": self._side,
            "spake2": json.loads(self._sp.serialize().decode("ascii")),
            "msg1": hexlify(self._msg1).decode("ascii"),
        }
        return json.dumps(data)

    # entry point 3: resume a previously-serialized session
    @classmethod
    def from_serialized(klass, data):
        d = json.loads(data)
        self = klass(d["appid"], d["relay_url"])
        self._side = d["side"]
        self._channelid = d["channelid"]
        self._set_code(d["code"])
        sp_data = json.dumps(d["spake2"]).encode("ascii")
        self._sp = SPAKE2_Symmetric.from_serialized(sp_data)
        self._msg1 = unhexlify(d["msg1"].encode("ascii"))
        return self

    @inlineCallbacks
    def get_verifier(self):
        if self._closed: raise UsageError
        if self._code is None: raise UsageError
        with self._timing.add("API get_verifier"):
            yield self._get_master_key()
            # If the caller cares about the verifier, then they'll probably
            # also willing to wait a moment to see the _confirm message. Each
            # side sends this as soon as it sees the other's PAKE message. So
            # the sender should see this hot on the heels of the inbound PAKE
            # message (a moment after _get_master_key() returns). The
            # receiver will see this a round-trip after they send their PAKE
            # (because the sender is using wait=True inside _get_master_key,
            # below: otherwise the sender might go do some blocking call).
            yield self._msg_get(u"_confirm")
        returnValue(self._verifier)

    @inlineCallbacks
    def _get_master_key(self):
        # TODO: prevent multiple invocation
        if not self._key:
            yield self._claim_channel_and_watch()
            yield self._msg_send(u"pake", self._msg1)
            pake_msg = yield self._msg_get(u"pake")

            with self._timing.add("pake2", waiting="crypto"):
                self._key = self._sp.finish(pake_msg)
            self._verifier = self.derive_key(u"wormhole:verifier")
            self._timing.add("key established")

            if self._send_confirm:
                # both sides send different (random) confirmation messages
                confkey = self.derive_key(u"wormhole:confirmation")
                nonce = os.urandom(CONFMSG_NONCE_LENGTH)
                confmsg = make_confmsg(confkey, nonce)
                yield self._msg_send(u"_confirm", confmsg, wait=True)

    @inlineCallbacks
    def _msg_send(self, phase, body, wait=False):
        self._sent_messages.add( (phase, body) )
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        t = self._timing.add("add", phase=phase, wait=wait)
        yield self._ws_send(u"add", phase=phase,
                            body=hexlify(body).decode("ascii"))
        if wait:
            while (phase, body) not in self._delivered_messages:
                yield self._sleep()
            t.finish()

    def _ws_handle_message(self, msg):
        m = msg["message"]
        phase = m["phase"]
        body = unhexlify(m["body"].encode("ascii"))
        if (phase, body) in self._sent_messages:
            self._delivered_messages.add( (phase, body) ) # ack by server
            self._wakeup()
            return # ignore echoes of our outbound messages
        if phase in self._received_messages:
            # a channel collision would cause this
            err = ServerError("got duplicate phase %s" % phase, self._ws_url)
            return self._signal_error(err)
        self._received_messages[phase] = body
        if phase == u"_confirm":
            # TODO: we might not have a master key yet, if the caller wasn't
            # waiting in _get_master_key() when a back-to-back pake+_confirm
            # message pair arrived.
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = body[:CONFMSG_NONCE_LENGTH]
            if body != make_confmsg(confkey, nonce):
                # this makes all API calls fail
                return self._signal_error(WrongPasswordError())
        # now notify anyone waiting on it
        self._wakeup()

    @inlineCallbacks
    def _msg_get(self, phase):
        with self._timing.add("get", phase=phase):
            while phase not in self._received_messages:
                yield self._sleep() # we can wait a long time here
                # that will throw an error if something goes wrong
            msg = self._received_messages[phase]
        returnValue(msg)

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(u"")): raise TypeError(type(purpose))
        if self._key is None:
            # call after get_verifier() or get_data()
            raise UsageError
        return HKDF(self._key, length, CTXinfo=to_bytes(purpose))

    def _encrypt_data(self, key, data):
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(data, type(b"")), type(data)
        assert len(key) == SecretBox.KEY_SIZE, len(key)
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(data, nonce)

    def _decrypt_data(self, key, encrypted):
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(encrypted, type(b"")), type(encrypted)
        assert len(key) == SecretBox.KEY_SIZE, len(key)
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data

    @inlineCallbacks
    def send_data(self, outbound_data, phase=u"data", wait=False):
        if not isinstance(outbound_data, type(b"")):
            raise TypeError(type(outbound_data))
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if self._closed: raise UsageError
        if self._code is None:
            raise UsageError("You must set_code() before send_data()")
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if phase in self._sent_phases: raise UsageError # only call this once
        self._sent_phases.add(phase)
        with self._timing.add("API send_data", phase=phase, wait=wait):
            # Without predefined roles, we can't derive predictably unique
            # keys for each side, so we use the same key for both. We use
            # random nonces to keep the messages distinct, and we
            # automatically ignore reflections.
            yield self._get_master_key()
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            outbound_encrypted = self._encrypt_data(data_key, outbound_data)
            yield self._msg_send(phase, outbound_encrypted, wait)

    @inlineCallbacks
    def get_data(self, phase=u"data"):
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if self._closed: raise UsageError
        if self._code is None: raise UsageError
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if phase in self._got_phases: raise UsageError # only call this once
        self._got_phases.add(phase)
        with self._timing.add("API get_data", phase=phase):
            yield self._get_master_key()
            body = yield self._msg_get(phase) # we can wait a long time here
        try:
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            inbound_data = self._decrypt_data(data_key, body)
            returnValue(inbound_data)
        except CryptoError:
            raise WrongPasswordError

    def _ws_closed(self, wasClean, code, reason):
        self._ws = None
        self._ws_t.finish()
        # TODO: schedule reconnect, unless we're done

    @inlineCallbacks
    def close(self, f=None, mood=None):
        """Do d.addBoth(w.close) at the end of your chain."""
        if self._closed:
            returnValue(None)
        self._closed = True
        if not self._ws:
            returnValue(None)

        if mood is None:
            mood = u"happy"
        if f:
            if f.check(Timeout):
                mood = u"lonely"
            elif f.check(WrongPasswordError):
                mood = u"scary"
            elif f.check(TypeError, UsageError):
                # preconditions don't warrant reporting mood
                pass
            else:
                mood = u"errory" # other errors do
        if not isinstance(mood, (type(None), type(u""))):
            raise TypeError(type(mood))

        with self._timing.add("API close"):
            yield self._deallocate(mood)
            # TODO: mark WebSocket as don't-reconnect
            self._ws.transport.loseConnection() # probably flushes
            del self._ws
            self._ws_t.finish()
            self._timing_started.finish(mood=mood)
        returnValue(f)

    @inlineCallbacks
    def _deallocate(self, mood):
        with self._timing.add("deallocate"):
            yield self._ws_send(u"deallocate", mood=mood)
            while self._deallocated_status is None:
                yield self._sleep(wake_on_error=False)
        # TODO: set a timeout, don't wait forever for an ack
        # TODO: if the connection is lost, let it go
        returnValue(self._deallocated_status)

    def _ws_handle_deallocated(self, msg):
        self._deallocated_status = msg["status"]
        self._wakeup()
