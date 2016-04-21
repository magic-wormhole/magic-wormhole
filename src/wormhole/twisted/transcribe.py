from __future__ import print_function
import os, sys, json, re, unicodedata
from six.moves.urllib_parse import urlparse
from binascii import hexlify, unhexlify
from twisted.internet import reactor, defer, endpoints, error
from twisted.internet.threads import deferToThread, blockingCallFromThread
from twisted.internet.defer import inlineCallbacks, returnValue
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

def close_on_error(meth): # method decorator
    # Clients report certain errors as "moods", so the server can make a
    # rough count failed connections (due to mismatched passwords, attacks,
    # or timeouts). We don't report precondition failures, as those are the
    # responsibility/fault of the local application code. We count
    # non-precondition errors in case they represent server-side problems.
    def _wrapper(self, *args, **kwargs):
        d = defer.maybeDeferred(meth, self, *args, **kwargs)
        def _onerror(f):
            if f.check(Timeout):
                d2 = self.close(u"lonely")
            elif f.check(WrongPasswordError):
                d2 = self.close(u"scary")
            elif f.check(TypeError, UsageError):
                # preconditions don't warrant _close_with_error()
                d2 = defer.succeed(None)
            else:
                d2 = self.close(u"errory")
            d2.addBoth(lambda _: f)
            return d2
        d.addErrback(_onerror)
        return d
    return _wrapper

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
        self._timing_started = self._timing.add_event("wormhole")
        self._ws = None
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
            # f.d is errbacked if WebSocket negotiation fails
            yield f.d # WebSocket drops data sent before onOpen() fires
            self._ws_send(u"bind", appid=self._appid, side=self._side)
        # the socket is connected, and bound, but no channel has been claimed
        returnValue(self._ws)

    @inlineCallbacks
    def _ws_send(self, mtype, **kwargs):
        ws = yield self._get_websocket()
        kwargs["type"] = mtype
        payload = json.dumps(kwargs).encode("utf-8")
        ws.sendMessage(payload, False)

    def _ws_dispatch_msg(self, payload):
        msg = json.loads(payload.decode("utf-8"))
        mtype = msg["type"]
        meth = getattr(self, "_ws_handle_"+mtype, None)
        if not meth:
            raise ValueError("Unknown inbound message type %r" % (msg,))
        return meth(msg)

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
    def _sleep(self):
        if self._error: # don't sleep if the bed's already on fire
            raise self._error
        d = defer.Deferred()
        self._sleepers.append(d)
        yield d
        if self._error:
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
        _sent = self._timing.add_event("allocate")
        yield self._ws_send(u"allocate")
        while self._channelid is None:
            yield self._sleep()
        self._timing.finish_event(_sent)
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
        initial_channelids = yield self._list_channels()
        _start = self._timing.add_event("input code", waiting="user")
        code = yield deferToThread(codes.input_code_with_completion,
                                   prompt,
                                   initial_channelids, _lister,
                                   code_length)
        self._timing.finish_event(_start)
        returnValue(code) # application will give this to set_code()

    @inlineCallbacks
    def _list_channels(self):
        _sent = self._timing.add_event("list")
        self._latest_channelids = None
        yield self._ws_send(u"list")
        while self._latest_channelids is None:
            yield self._sleep()
        self._timing.finish_event(_sent)
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
        self._channelid = int(mo.group(1))
        self._set_code(code)
        self._start()

    def _set_code(self, code):
        if self._code is not None: raise UsageError
        self._timing.add_event("code established")
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

    @close_on_error
    @inlineCallbacks
    def get_verifier(self):
        if self._closed: raise UsageError
        if self._code is None: raise UsageError
        yield self._get_master_key()
        returnValue(self._verifier)

    @inlineCallbacks
    def _get_master_key(self):
        # TODO: prevent multiple invocation
        if not self._key:
            yield self._claim_channel_and_watch()
            yield self._msg_send(u"pake", self._msg1)
            pake_msg = yield self._msg_get(u"pake")

            self._key = self._sp.finish(pake_msg)
            self._verifier = self.derive_key(u"wormhole:verifier")
            self._timing.add_event("key established")

            if self._send_confirm:
                # both sides send different (random) confirmation messages
                confkey = self.derive_key(u"wormhole:confirmation")
                nonce = os.urandom(CONFMSG_NONCE_LENGTH)
                confmsg = make_confmsg(confkey, nonce)
                yield self._msg_send(u"_confirm", confmsg)

    @inlineCallbacks
    def _msg_send(self, phase, body, wait=False):
        self._sent_messages.add( (phase, body) )
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        yield self._ws_send(u"add", phase=phase,
                            body=hexlify(body).decode("ascii"))
        if wait:
            while (phase, body) not in self._delivered_messages:
                yield self._sleep()

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
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = body[:CONFMSG_NONCE_LENGTH]
            if body != make_confmsg(confkey, nonce):
                # this makes all API calls fail
                return self._signal_error(WrongPasswordError())
        # now notify anyone waiting on it
        self._wakeup()

    @inlineCallbacks
    def _msg_get(self, phase):
        _start = self._timing.add_event("get(%s)" % phase)
        while phase not in self._received_messages:
            yield self._sleep() # we can wait a long time here
            # that will throw an error if something goes wrong
        self._timing.finish_event(_start)
        returnValue(self._received_messages[phase])

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

    @close_on_error
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
        _sent = self._timing.add_event("API send data", phase=phase, wait=wait)
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and we automatically ignore
        # reflections.
        yield self._get_master_key()
        data_key = self.derive_key(u"wormhole:phase:%s" % phase)
        outbound_encrypted = self._encrypt_data(data_key, outbound_data)
        yield self._msg_send(phase, outbound_encrypted, wait)
        self._timing.finish_event(_sent)

    @close_on_error
    @inlineCallbacks
    def get_data(self, phase=u"data"):
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if self._closed: raise UsageError
        if self._code is None: raise UsageError
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if phase in self._got_phases: raise UsageError # only call this once
        self._got_phases.add(phase)
        _sent = self._timing.add_event("API get data", phase=phase)
        yield self._get_master_key()
        body = yield self._msg_get(phase) # we can wait a long time here
        self._timing.finish_event(_sent)
        try:
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            inbound_data = self._decrypt_data(data_key, body)
            returnValue(inbound_data)
        except CryptoError:
            raise WrongPasswordError

    def _ws_closed(self, wasClean, code, reason):
        self._ws = None
        # TODO: schedule reconnect, unless we're done

    @inlineCallbacks
    def close(self, res=None, mood=u"happy"):
        if not isinstance(mood, (type(None), type(u""))):
            raise TypeError(type(mood))
        if self._closed:
            returnValue(None)
        self._closed = True
        if not self._ws:
            returnValue(None)
        self._timing.finish_event(self._timing_started, mood=mood)
        yield self._deallocate(mood)
        # TODO: mark WebSocket as don't-reconnect
        self._ws.transport.loseConnection() # probably flushes
        del self._ws

    @inlineCallbacks
    def _deallocate(self, mood=None):
        _sent = self._timing.add_event("close")
        yield self._ws_send(u"deallocate", mood=mood)
        while self._deallocated_status is None:
            yield self._sleep()
        self._timing.finish_event(_sent)
        # TODO: set a timeout, don't wait forever for an ack
        # TODO: if the connection is lost, let it go
        returnValue(self._deallocated_status)

    def _ws_handle_deallocated(self, msg):
        self._deallocated_status = msg["status"]
        self._wakeup()
