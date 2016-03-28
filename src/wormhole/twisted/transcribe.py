from __future__ import print_function
import os, sys, json, re, unicodedata
from six.moves.urllib_parse import urlencode
from binascii import hexlify, unhexlify
from zope.interface import implementer
from twisted.internet import reactor, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web import client as web_client
from twisted.web import error as web_error
from twisted.web.iweb import IBodyProducer
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from spake2 import SPAKE2_Symmetric
from .eventsource_twisted import ReconnectingEventSource
from .. import __version__
from .. import codes
from ..errors import ServerError, Timeout, WrongPasswordError, UsageError
from ..timing import DebugTiming
from ..util.hkdf import HKDF
from ..channel_monitor import monitor

CONFMSG_NONCE_LENGTH = 128//8
CONFMSG_MAC_LENGTH = 256//8
def make_confmsg(confkey, nonce):
    return nonce+HKDF(confkey, CONFMSG_MAC_LENGTH, nonce)

def to_bytes(u):
    return unicodedata.normalize("NFC", u).encode("utf-8")

@implementer(IBodyProducer)
class DataProducer:
    def __init__(self, data):
        self.data = data
        self.length = len(data)
    def startProducing(self, consumer):
        consumer.write(self.data)
        return defer.succeed(None)
    def stopProducing(self):
        pass
    def pauseProducing(self):
        pass
    def resumeProducing(self):
        pass


def post_json(agent, url, request_body):
    # POST a JSON body to a URL, parsing the response as JSON
    data = json.dumps(request_body).encode("utf-8")
    d = agent.request(b"POST", url.encode("utf-8"),
                      bodyProducer=DataProducer(data))
    def _check_error(resp):
        if resp.code != 200:
            raise web_error.Error(resp.code, resp.phrase)
        return resp
    d.addCallback(_check_error)
    d.addCallback(web_client.readBody)
    d.addCallback(lambda data: json.loads(data.decode("utf-8")))
    return d

def get_json(agent, url):
    # GET from a URL, parsing the response as JSON
    d = agent.request(b"GET", url.encode("utf-8"))
    def _check_error(resp):
        if resp.code != 200:
            raise web_error.Error(resp.code, resp.phrase)
        return resp
    d.addCallback(_check_error)
    d.addCallback(web_client.readBody)
    d.addCallback(lambda data: json.loads(data.decode("utf-8")))
    return d

class Channel:
    def __init__(self, relay_url, appid, channelid, side, handle_welcome,
                 agent, timing):
        self._relay_url = relay_url
        self._appid = appid
        self._channelid = channelid
        self._side = side
        self._handle_welcome = handle_welcome
        self._agent = agent
        self._timing = timing
        self._messages = set() # (phase,body) , body is bytes
        self._sent_messages = set() # (phase,body)

    def _add_inbound_messages(self, messages):
        for msg in messages:
            phase = msg["phase"]
            body = unhexlify(msg["body"].encode("ascii"))
            self._messages.add( (phase, body) )

    def _find_inbound_message(self, phases):
        their_messages = self._messages - self._sent_messages
        for phase in phases:
            for (their_phase,body) in their_messages:
                if their_phase == phase:
                    return (phase, body)
        return None

    def send(self, phase, msg):
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if not isinstance(msg, type(b"")): raise TypeError(type(msg))
        self._sent_messages.add( (phase,msg) )
        assert isinstance(self._side, type(u"")), type(self._side)
        payload = {"appid": self._appid,
                   "channelid": self._channelid,
                   "side": self._side,
                   "phase": phase,
                   "body": hexlify(msg).decode("ascii")}
        _sent = self._timing.add_event("send %s" % phase)
        d = post_json(self._agent, self._relay_url+"add", payload)
        def _maybe_handle_welcome(resp):
            self._timing.finish_event(_sent, resp.get("sent"))
            if "welcome" in resp:
                self._handle_welcome(resp["welcome"])
            return resp
        d.addCallback(_maybe_handle_welcome)
        d.addCallback(lambda resp: self._add_inbound_messages(resp["messages"]))
        return d

    def get_first_of(self, phases):
        if not isinstance(phases, (list, set)): raise TypeError(type(phases))
        for phase in phases:
            if not isinstance(phase, type(u"")): raise TypeError(type(phase))

        # fire with a bytestring of the first message for any 'phase' that
        # wasn't one of our own messages. It will either come from
        # previously-received messages, or from an EventSource that we attach
        # to the corresponding URL
        _sent = self._timing.add_event("get %s" % "/".join(sorted(phases)))

        phase_and_body = self._find_inbound_message(phases)
        if phase_and_body is not None:
            self._timing.finish_event(_sent)
            return defer.succeed(phase_and_body)
        d = defer.Deferred()
        msgs = []
        def _handle(name, line):
            if name == "welcome":
                self._handle_welcome(json.loads(line))
            if name == "message":
                data = json.loads(line)
                self._add_inbound_messages([data])
                phase_and_body = self._find_inbound_message(phases)
                if phase_and_body is not None and not msgs:
                    msgs.append(phase_and_body)
                    self._timing.finish_event(_sent, data.get("sent"))
                    d.callback(None)
        queryargs = urlencode([("appid", self._appid),
                               ("channelid", self._channelid)])
        es = ReconnectingEventSource(self._relay_url+"watch?%s" % queryargs,
                                     _handle, self._agent)
        es.startService() # TODO: .setServiceParent(self)
        es.activate()
        d.addCallback(lambda _: es.deactivate())
        d.addCallback(lambda _: es.stopService())
        d.addCallback(lambda _: msgs[0])
        return d

    @inlineCallbacks
    def get(self, phase):
        res = yield self.get_first_of([phase])
        (got_phase, body) = res
        assert got_phase == phase
        returnValue(body)

    def deallocate(self, mood=None):
        # only try once, no retries
        _sent = self._timing.add_event("close")
        d = post_json(self._agent, self._relay_url+"deallocate",
                      {"appid": self._appid,
                       "channelid": self._channelid,
                       "side": self._side,
                       "mood": mood})
        def _done(resp):
            self._timing.finish_event(_sent, resp.get("sent"))
        d.addCallback(_done)
        d.addBoth(lambda _: None) # ignore POST failure
        return d

class ChannelManager:
    def __init__(self, relay, appid, side, handle_welcome, tor_manager=None,
                 timing=None):
        assert isinstance(relay, type(u""))
        self._relay = relay
        self._appid = appid
        self._side = side
        self._handle_welcome = handle_welcome
        self._pool = web_client.HTTPConnectionPool(reactor, True) # persistent
        if tor_manager:
            print("ChannelManager using tor")
            epf = tor_manager.get_web_agent_endpoint_factory()
            agent = web_client.Agent.usingEndpointFactory(reactor, epf,
                                                          pool=self._pool)
        else:
            agent = web_client.Agent(reactor, pool=self._pool)
        self._agent = agent
        self._timing = timing or DebugTiming()

    @inlineCallbacks
    def allocate(self):
        url = self._relay + "allocate"
        _sent = self._timing.add_event("allocate")
        data = yield post_json(self._agent, url, {"appid": self._appid,
                                                  "side": self._side})
        if "welcome" in data:
            self._handle_welcome(data["welcome"])
        self._timing.finish_event(_sent, data.get("sent"))
        returnValue(data["channelid"])

    @inlineCallbacks
    def list_channels(self):
        queryargs = urlencode([("appid", self._appid)])
        url = self._relay + u"list?%s" % queryargs
        _sent = self._timing.add_event("list")
        r = yield get_json(self._agent, url)
        self._timing.finish_event(_sent, r.get("sent"))
        returnValue(r["channelids"])

    def connect(self, channelid):
        return Channel(self._relay, self._appid, channelid, self._side,
                       self._handle_welcome, self._agent, self._timing)

    @inlineCallbacks
    def shutdown(self):
        _sent = self._timing.add_event("pool shutdown")
        yield self._pool.closeCachedConnections()
        self._timing.finish_event(_sent)

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

class Wormhole:
    motd_displayed = False
    version_warning_displayed = False
    _send_confirm = True

    def __init__(self, appid, relay_url, tor_manager=None, timing=None):
        if not isinstance(appid, type(u"")): raise TypeError(type(appid))
        if not isinstance(relay_url, type(u"")):
            raise TypeError(type(relay_url))
        if not relay_url.endswith(u"/"): raise UsageError
        self._appid = appid
        self._relay_url = relay_url
        self._tor_manager = tor_manager
        self._timing = timing or DebugTiming()
        self._set_side(hexlify(os.urandom(5)).decode("ascii"))
        self.code = None
        self.key = None
        self._started_get_code = False
        self._sent_data = set() # phases
        self._got_data = set()
        self._got_confirmation = False
        self._closed = False
        self._timing_started = self._timing.add_event("wormhole")

    def _set_side(self, side):
        self._side = side
        self._channel_manager = ChannelManager(self._relay_url, self._appid,
                                               self._side, self.handle_welcome,
                                               self._tor_manager,
                                               self._timing)
        self._channel = None

    def handle_welcome(self, welcome):
        if ("motd" in welcome and
            not self.motd_displayed):
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" %
                  (self._relay_url, motd_formatted), file=sys.stderr)
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
            raise ServerError(welcome["error"], self._relay_url)

    @inlineCallbacks
    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        channelid = yield self._channel_manager.allocate()
        code = codes.make_code(channelid, code_length)
        assert isinstance(code, type(u"")), type(code)
        self._set_code_and_channelid(code)
        self._start()
        returnValue(code)

    def set_code(self, code):
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self.code is not None: raise UsageError
        self._set_code_and_channelid(code)
        self._start()

    def _set_code_and_channelid(self, code):
        if self.code is not None: raise UsageError
        self._timing.add_event("code established")
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        self.code = code
        channelid = int(mo.group(1))
        self._channel = self._channel_manager.connect(channelid)
        monitor.add(self._channel)

    def _start(self):
        # allocate the rest now too, so it can be serialized
        self.sp = SPAKE2_Symmetric(to_bytes(self.code),
                                   idSymmetric=to_bytes(self._appid))
        self.msg1 = self.sp.start()

    def serialize(self):
        # I can only be serialized after get_code/set_code and before
        # get_verifier/get_data
        if self.code is None: raise UsageError
        if self.key is not None: raise UsageError
        if self._sent_data: raise UsageError
        if self._got_data: raise UsageError
        data = {
            "appid": self._appid,
            "relay_url": self._relay_url,
            "code": self.code,
            "side": self._side,
            "spake2": json.loads(self.sp.serialize().decode("ascii")),
            "msg1": hexlify(self.msg1).decode("ascii"),
        }
        return json.dumps(data)

    @classmethod
    def from_serialized(klass, data):
        d = json.loads(data)
        self = klass(d["appid"], d["relay_url"])
        self._set_side(d["side"])
        self._set_code_and_channelid(d["code"])
        sp_data = json.dumps(d["spake2"]).encode("ascii")
        self.sp = SPAKE2_Symmetric.from_serialized(sp_data)
        self.msg1 = unhexlify(d["msg1"].encode("ascii"))
        return self

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(u"")): raise TypeError(type(purpose))
        if self.key is None:
            # call after get_verifier() or get_data()
            raise UsageError
        return HKDF(self.key, length, CTXinfo=to_bytes(purpose))

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
    def _get_key(self):
        # TODO: prevent multiple invocation
        if self.key:
            returnValue(self.key)
        yield self._channel.send(u"pake", self.msg1)
        pake_msg = yield self._channel.get(u"pake")

        key = self.sp.finish(pake_msg)
        self.key = key
        self.verifier = self.derive_key(u"wormhole:verifier")
        self._timing.add_event("key established")

        if not self._send_confirm:
            returnValue(key)
        confkey = self.derive_key(u"wormhole:confirmation")
        nonce = os.urandom(CONFMSG_NONCE_LENGTH)
        confmsg = make_confmsg(confkey, nonce)
        yield self._channel.send(u"_confirm", confmsg)
        returnValue(key)

    @close_on_error
    @inlineCallbacks
    def get_verifier(self):
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        yield self._get_key()
        returnValue(self.verifier)

    @close_on_error
    @inlineCallbacks
    def send_data(self, outbound_data, phase=u"data"):
        if not isinstance(outbound_data, type(b"")):
            raise TypeError(type(outbound_data))
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if self._closed: raise UsageError
        if phase in self._sent_data: raise UsageError # only call this once
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        _sent = self._timing.add_event("API send data", phase=phase)
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and the Channel automatically
        # ignores reflections.
        self._sent_data.add(phase)
        yield self._get_key()
        data_key = self.derive_key(u"wormhole:phase:%s" % phase)
        outbound_encrypted = self._encrypt_data(data_key, outbound_data)
        yield self._channel.send(phase, outbound_encrypted)
        self._timing.finish_event(_sent)

    @close_on_error
    @inlineCallbacks
    def get_data(self, phase=u"data"):
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if phase in self._got_data: raise UsageError # only call this once
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        _sent = self._timing.add_event("API get data", phase=phase)
        self._got_data.add(phase)
        yield self._get_key()
        phases = []
        if not self._got_confirmation:
            phases.append(u"_confirm")
        phases.append(phase)
        phase_and_body = yield self._channel.get_first_of(phases)
        (got_phase, body) = phase_and_body
        if got_phase == u"_confirm":
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = body[:CONFMSG_NONCE_LENGTH]
            if body != make_confmsg(confkey, nonce):
                raise WrongPasswordError
            self._got_confirmation = True
            phase_and_body = yield self._channel.get_first_of([phase])
            (got_phase, body) = phase_and_body
        self._timing.finish_event(_sent)
        assert got_phase == phase
        try:
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            inbound_data = self._decrypt_data(data_key, body)
            returnValue(inbound_data)
        except CryptoError:
            raise WrongPasswordError

    @inlineCallbacks
    def close(self, res=None, mood=u"happy"):
        if not isinstance(mood, (type(None), type(u""))):
            raise TypeError(type(mood))
        self._closed = True
        if not self._channel:
            returnValue(None)
        self._timing.finish_event(self._timing_started, mood=mood)
        c, self._channel = self._channel, None
        monitor.close(c)
        yield c.deallocate(mood)
        yield self._channel_manager.shutdown()

