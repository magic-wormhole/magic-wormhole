from __future__ import print_function
import os, sys, json, re, unicodedata
from six.moves.urllib_parse import urlencode
from binascii import hexlify, unhexlify
from zope.interface import implementer
from twisted.internet import reactor, defer
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
                 agent):
        self._relay_url = relay_url
        self._appid = appid
        self._channelid = channelid
        self._side = side
        self._handle_welcome = handle_welcome
        self._agent = agent
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
        d = post_json(self._agent, self._relay_url+"add", payload)
        def _maybe_handle_welcome(resp):
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
        phase_and_body = self._find_inbound_message(phases)
        if phase_and_body is not None:
            return defer.succeed(phase_and_body)
        d = defer.Deferred()
        msgs = []
        def _handle(name, data):
            if name == "welcome":
                self._handle_welcome(json.loads(data))
            if name == "message":
                self._add_inbound_messages([json.loads(data)])
                phase_and_body = self._find_inbound_message(phases)
                if phase_and_body is not None and not msgs:
                    msgs.append(phase_and_body)
                    d.callback(None)
        # TODO: use agent=self._agent
        queryargs = urlencode([("appid", self._appid),
                               ("channelid", self._channelid)])
        es = ReconnectingEventSource(self._relay_url+"watch?%s" % queryargs,
                                     _handle)
        es.startService() # TODO: .setServiceParent(self)
        es.activate()
        d.addCallback(lambda _: es.deactivate())
        d.addCallback(lambda _: es.stopService())
        d.addCallback(lambda _: msgs[0])
        return d

    def get(self, phase):
        d = self.get_first_of([phase])
        def _got(res):
            (got_phase, body) = res
            assert got_phase == phase
            return body
        d.addCallback(_got)
        return d

    def deallocate(self, mood=None):
        # only try once, no retries
        d = post_json(self._agent, self._relay_url+"deallocate",
                      {"appid": self._appid,
                       "channelid": self._channelid,
                       "side": self._side,
                       "mood": mood})
        d.addBoth(lambda _: None) # ignore POST failure
        return d

class ChannelManager:
    def __init__(self, relay, appid, side, handle_welcome):
        assert isinstance(relay, type(u""))
        self._relay = relay
        self._appid = appid
        self._side = side
        self._handle_welcome = handle_welcome
        self._agent = web_client.Agent(reactor)

    def allocate(self):
        url = self._relay + "allocate"
        d = post_json(self._agent, url, {"appid": self._appid,
                                         "side": self._side})
        def _got_channel(data):
            if "welcome" in data:
                self._handle_welcome(data["welcome"])
            return data["channelid"]
        d.addCallback(_got_channel)
        return d

    def list_channels(self):
        queryargs = urlencode([("appid", self._appid)])
        url = self._relay + u"list?%s" % queryargs
        d = get_json(self._agent, url)
        d.addCallback(lambda r: r["channelids"])
        return d

    def connect(self, channelid):
        return Channel(self._relay, self._appid, channelid, self._side,
                       self._handle_welcome, self._agent)


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

    def __init__(self, appid, relay_url):
        if not isinstance(appid, type(u"")): raise TypeError(type(appid))
        if not isinstance(relay_url, type(u"")):
            raise TypeError(type(relay_url))
        if not relay_url.endswith(u"/"): raise UsageError
        self._appid = appid
        self._relay_url = relay_url
        self._set_side(hexlify(os.urandom(5)).decode("ascii"))
        self.code = None
        self.key = None
        self._started_get_code = False
        self._sent_data = set() # phases
        self._got_data = set()
        self._got_confirmation = False
        self._closed = False

    def _set_side(self, side):
        self._side = side
        self._channel_manager = ChannelManager(self._relay_url, self._appid,
                                               self._side, self.handle_welcome)
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

    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        d = self._channel_manager.allocate()
        def _got_channelid(channelid):
            code = codes.make_code(channelid, code_length)
            assert isinstance(code, type(u"")), type(code)
            self._set_code_and_channelid(code)
            self._start()
            return code
        d.addCallback(_got_channelid)
        return d

    def set_code(self, code):
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self.code is not None: raise UsageError
        self._set_code_and_channelid(code)
        self._start()

    def _set_code_and_channelid(self, code):
        if self.code is not None: raise UsageError
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


    def _get_key(self):
        # TODO: prevent multiple invocation
        if self.key:
            return defer.succeed(self.key)
        d = self._channel.send(u"pake", self.msg1)
        d.addCallback(lambda _: self._channel.get(u"pake"))
        def _got_pake(pake_msg):
            key = self.sp.finish(pake_msg)
            self.key = key
            self.verifier = self.derive_key(u"wormhole:verifier")
            if not self._send_confirm:
                return key
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = os.urandom(CONFMSG_NONCE_LENGTH)
            confmsg = make_confmsg(confkey, nonce)
            d1 = self._channel.send(u"_confirm", confmsg)
            d1.addCallback(lambda _: key)
            return d1
        d.addCallback(_got_pake)
        return d

    @close_on_error
    def get_verifier(self):
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        d = self._get_key()
        d.addCallback(lambda _: self.verifier)
        return d

    @close_on_error
    def send_data(self, outbound_data, phase=u"data"):
        if not isinstance(outbound_data, type(b"")):
            raise TypeError(type(outbound_data))
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if self._closed: raise UsageError
        if phase in self._sent_data: raise UsageError # only call this once
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and the Channel automatically
        # ignores reflections.
        self._sent_data.add(phase)
        d = self._get_key()
        def _send(key):
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            outbound_encrypted = self._encrypt_data(data_key, outbound_data)
            return self._channel.send(phase, outbound_encrypted)
        d.addCallback(_send)
        return d

    @close_on_error
    def get_data(self, phase=u"data"):
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if phase in self._got_data: raise UsageError # only call this once
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        self._got_data.add(phase)
        d = self._get_key()
        def _get(key):
            phases = []
            if not self._got_confirmation:
                phases.append(u"_confirm")
            phases.append(phase)
            d1 = self._channel.get_first_of(phases)
            def _maybe_got_confirm(phase_and_body):
                (got_phase, body) = phase_and_body
                if got_phase == u"_confirm":
                    confkey = self.derive_key(u"wormhole:confirmation")
                    nonce = body[:CONFMSG_NONCE_LENGTH]
                    if body != make_confmsg(confkey, nonce):
                        raise WrongPasswordError
                    self._got_confirmation = True
                    return self._channel.get_first_of([phase])
                return phase_and_body
            d1.addCallback(_maybe_got_confirm)
            def _got(phase_and_body):
                (got_phase, body) = phase_and_body
                assert got_phase == phase
                try:
                    data_key = self.derive_key(u"wormhole:phase:%s" % phase)
                    inbound_data = self._decrypt_data(data_key, body)
                    return inbound_data
                except CryptoError:
                    raise WrongPasswordError
            d1.addCallback(_got)
            return d1
        d.addCallback(_get)
        return d

    def close(self, res=None, mood=u"happy"):
        if not isinstance(mood, (type(None), type(u""))):
            raise TypeError(type(mood))
        self._closed = True
        d = defer.succeed(None)
        if self._channel:
            c, self._channel = self._channel, None
            monitor.close(c)
            d.addCallback(lambda _: c.deallocate(mood))
        return d
