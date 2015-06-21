from __future__ import print_function
import os, sys, json, re
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
from .eventsource import ReconnectingEventSource
from .. import __version__
from .. import codes
from ..errors import ServerError
from ..util.hkdf import HKDF

class WrongPasswordError(Exception):
    """
    Key confirmation failed.
    """

class ReflectionAttack(Exception):
    """An attacker (or bug) reflected our outgoing message back to us."""

class UsageError(Exception):
    """The programmer did something wrong."""

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


class SymmetricWormhole:
    def __init__(self, appid, relay):
        self.appid = appid
        self.relay = relay
        self.agent = web_client.Agent(reactor)
        self.side = None
        self.code = None
        self.key = None
        self._started_get_code = False

    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        if self._started_get_code: raise UsageError
        self._started_get_code = True
        self.side = hexlify(os.urandom(5))
        d = self._allocate_channel()
        def _got_channel_id(channel_id):
            code = codes.make_code(channel_id, code_length)
            self._set_code_and_channel_id(code)
            self._start()
            return code
        d.addCallback(_got_channel_id)
        return d

    def _allocate_channel(self):
        url = self.relay + "allocate/%s" % self.side
        d = self.post(url)
        def _got_channel(data):
            if "welcome" in data:
                self.handle_welcome(data["welcome"])
            return data["channel-id"]
        d.addCallback(_got_channel)
        return d

    def set_code(self, code):
        if self.code is not None: raise UsageError
        if self.side is not None: raise UsageError
        self._set_code_and_channel_id(code)
        self.side = hexlify(os.urandom(5))
        self._start()

    def _set_code_and_channel_id(self, code):
        if self.code is not None: raise UsageError
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        self.channel_id = int(mo.group(1))
        self.code = code

    def _start(self):
        # allocate the rest now too, so it can be serialized
        self.sp = SPAKE2_Symmetric(self.code.encode("ascii"),
                                   idA=self.appid+":SymmetricA",
                                   idB=self.appid+":SymmetricB")
        self.msg1 = self.sp.start()

    def serialize(self):
        # I can only be serialized after get_code/set_code and before
        # get_verifier/get_data
        if self.code is None: raise UsageError
        if self.key is not None: raise UsageError
        data = {
            "appid": self.appid,
            "relay": self.relay,
            "code": self.code,
            "side": self.side,
            "spake2": json.loads(self.sp.serialize()),
            "msg1": self.msg1.encode("hex"),
        }
        return json.dumps(data)

    @classmethod
    def from_serialized(klass, data):
        d = json.loads(data)
        self = klass(d["appid"].encode("ascii"), d["relay"].encode("ascii"))
        self._set_code_and_channel_id(d["code"].encode("ascii"))
        self.side = d["side"].encode("ascii")
        self.sp = SPAKE2_Symmetric.from_serialized(json.dumps(d["spake2"]))
        self.msg1 = d["msg1"].decode("hex")
        return self

    motd_displayed = False
    version_warning_displayed = False

    def handle_welcome(self, welcome):
        if ("motd" in welcome and
            not self.motd_displayed):
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" % (self.relay, motd_formatted),
                  file=sys.stderr)
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
            raise ServerError(welcome["error"], self.relay)

    def url(self, verb, msgnum=None):
        url = "%s%d/%s/%s" % (self.relay, self.channel_id, self.side, verb)
        if msgnum is not None:
            url += "/" + msgnum
        return url

    def post(self, url, post_json=None):
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        p = None
        if post_json:
            data = json.dumps(post_json).encode("utf-8")
            p = DataProducer(data)
        d = self.agent.request("POST", url, bodyProducer=p)
        def _check_error(resp):
            if resp.code != 200:
                raise web_error.Error(resp.code, resp.phrase)
            return resp
        d.addCallback(_check_error)
        d.addCallback(web_client.readBody)
        d.addCallback(lambda data: json.loads(data))
        return d

    def _get_msgs(self, old_msgs, verb, msgnum):
        # fire with a list of messages that match verb/msgnum, which either
        # came from old_msgs, or from an EventSource that we attached to the
        # corresponding URL
        if old_msgs:
            return defer.succeed(old_msgs)
        d = defer.Deferred()
        msgs = []
        def _handle(name, data):
            if name == "welcome":
                self.handle_welcome(json.loads(data))
            if name == "message":
                msgs.append(json.loads(data)["message"])
                d.callback(None)
        es = ReconnectingEventSource(None, lambda: self.url(verb, msgnum),
                                     _handle)#, agent=self.agent)
        es.startService() # TODO: .setServiceParent(self)
        es.activate()
        d.addCallback(lambda _: es.deactivate())
        d.addCallback(lambda _: es.stopService())
        d.addCallback(lambda _: msgs)
        return d

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        assert self.key is not None # call after get_verifier() or get_data()
        assert type(purpose) == type(b"")
        return HKDF(self.key, length, CTXinfo=purpose)

    def _encrypt_data(self, key, data):
        assert len(key) == SecretBox.KEY_SIZE
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(data, nonce)

    def _decrypt_data(self, key, encrypted):
        assert len(key) == SecretBox.KEY_SIZE
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data


    def _get_key(self):
        # TODO: prevent multiple invocation
        if self.key:
            return defer.succeed(self.key)
        data = {"message": hexlify(self.msg1).decode("ascii")}
        d = self.post(self.url("post", "pake"), data)
        d.addCallback(lambda j: self._get_msgs(j["messages"], "poll", "pake"))
        def _got_pake(msgs):
            pake_msg = unhexlify(msgs[0].encode("ascii"))
            key = self.sp.finish(pake_msg)
            self.key = key
            self.verifier = self.derive_key(self.appid+b":Verifier")
            return key
        d.addCallback(_got_pake)
        return d

    def get_verifier(self):
        if self.code is None: raise UsageError
        d = self._get_key()
        d.addCallback(lambda _: self.verifier)
        return d

    def get_data(self, outbound_data):
        # only call this once
        if self.code is None: raise UsageError
        d = self._get_key()
        d.addCallback(self._get_data2, outbound_data)
        return d

    def _get_data2(self, key, outbound_data):
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and check for reflection.
        data_key = self.derive_key(b"data-key")
        outbound_encrypted = self._encrypt_data(data_key, outbound_data)
        data = {"message": hexlify(outbound_encrypted).decode("ascii")}
        d = self.post(self.url("post", "data"), data)
        d.addCallback(lambda j: self._get_msgs(j["messages"], "poll", "data"))
        def _got_data(msgs):
            inbound_encrypted = unhexlify(msgs[0].encode("ascii"))
            if inbound_encrypted == outbound_encrypted:
                raise ReflectionAttack
            try:
                inbound_data = self._decrypt_data(data_key, inbound_encrypted)
                return inbound_data
            except CryptoError:
                raise WrongPasswordError
        d.addCallback(_got_data)
        d.addBoth(self._deallocate)
        return d

    def _deallocate(self, res):
        # only try once, no retries
        d = self.agent.request("POST", self.url("deallocate"))
        d.addBoth(lambda _: res) # ignore POST failure, pass-through result
        return d
