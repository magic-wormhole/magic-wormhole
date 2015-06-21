from __future__ import print_function
import sys, json
from binascii import hexlify, unhexlify
from zope.interface import implementer
#from twisted.application import service
from twisted.internet import reactor, defer
from twisted.web import client as web_client
from twisted.web import error as web_error
from twisted.web.iweb import IBodyProducer
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
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

@implementer(IBodyProducer)
class DataProducer:
    def __init__(self, data):
        self.data = data
    def startProducing(self, consumer):
        consumer.write(self.data)
        return defer.succeed(None)
    def stopProducing(self):
        pass
    def pauseProducing(self):
        pass
    def resumeProducing(self):
        pass

'''
class TwistedInitiator(service.MultiService):
    """I am a service, and I must be running to function. Either call my
    .startService() method, or .setServiceParent() me to some other running
    service. You can use i.when_done().addCallback(i.disownServiceParent) to
    make me go away when everything is done.
    """
    def __init__(self, appid, data, reactor, relay):
        self.appid = appid
        self.data = data
        self.reactor = reactor
        self.relay = relay
        self.code = None

    def set_code(self, code): # used for human-made pre-generated codes
        assert self.code is None
        mo = re.search(r'^(\d+)-', code)
        if not mo:
            raise ValueError("code (%s) must start with NN-" % code)
        self.channel_id = int(mo.group(1))
        self.code = code
        self.sp = SPAKE2_A(self.code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")

    def get_code(self, length=2):
        assert self.code is None
        d = self._allocate_channel()
        def _got_channel_id(channel_id):
            code = codes.make_code(channel_id, code_length)
            self.set_code(code)
            return code
        d.addCallback(_got_channel_id)
        return d

    def serialize(self):
        if not self.code:
            raise ValueEror

    def get_data(self, outbound_data):
        msg = self.sp.start()
        # change SPAKE2 to choose random_scalar earlier, to make getting the
        # first message idempotent.
        ...

    def when_get_code(self):
        pass # return Deferred

    def when_get_data(self):
        pass # return Deferred

class TwistedReceiver(service.MultiService):
    def __init__(self, appid, data, code, reactor, relay):
        self.appid = appid
        self.data = data
        self.code = code
        self.reactor = reactor
        self.relay = relay

    def when_get_data(self):
        pass # return Deferred
'''


class SymmetricWormhole:
    def __init__(self, appid, relay):
        self.appid = appid
        self.relay = relay
        self.agent = web_client.Agent(reactor)
        self.key = None

    def set_code(self, code):
        assert self.code is None
        self.code = code
        # allocate the rest now too, so it can be serialized
        self.sp = SPAKE2_Symmetric(self.code.encode("ascii"),
                                   idA=self.appid+":SymmetricA",
                                   idB=self.appid+":SymmetricB")
        self.msg1 = self.sp.start()

    def _allocate_channel(self):
        url = self.relay + "allocate/%s" % self.side
        d = self.post(url)
        def _got_channel(data_json):
            data = json.loads(data_json)
            if "welcome" in data:
                self.handle_welcome(data["welcome"])
            return data["channel-id"]
        d.addCallback(_got_channel)
        return d

    def _deallocate(self, res):
        d = self.agent.request("POST", self.url("deallocate"))
        d.addBoth(lambda _: res) # ignore POST failure, pass-through result
        return d

    def get_code(self, code_length=2):
        if self.code is not None:
            return defer.succeed(self.code)
        d = self._allocate_channel()
        def _got_channel_id(channel_id):
            code = codes.make_code(channel_id, code_length)
            self.set_code(code)
            return code
        d.addCallback(_got_channel_id)
        return d

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

    def get_msgs(self, old_msgs, verb, msgnum):
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
                msgs.extend(json.loads(data)["message"])
                d.callback(None)
        es = ReconnectingEventSource(None, lambda: self.url("post", "pake"),
                                     _handle)#, agent=self.agent)
        es.startService() # TODO: .setServiceParent(self)
        es.activate()
        d.addCallback(lambda _: es.deactivate())
        d.addCallback(lambda _: es.stopService())
        d.addCallback(lambda _: msgs)
        return d

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        assert type(purpose) == type(b"")
        return HKDF(self.key, length, CTXinfo=purpose)


    def _get_key(self):
        # TODO: prevent multiple invocation
        if self.key:
            return defer.succeed(self.key)
        data = {"message": hexlify(self.msg1).decode("ascii")}
        d = self.post(self.url("post", "pake"), data)
        d.addCallback(lambda j: self.get_msgs(j["messages"], "poll", "pake"))
        def _got_pake(msgs):
            pake_msg = unhexlify(msgs[0].encode("ascii"))
            key = self.sp.finish(pake_msg)
            self.key = key
            self.verifier = self.derive_key(self.appid+b":Verifier")
            return key
        d.addCallback(_got_pake)
        return d

    def get_verifier(self):
        d = self._get_key()
        d.addCallback(lambda _: self.verifier)
        return d

    def get_data(self, outbound_data):
        # only call this once
        d = self._get_key()
        def _got_key(_):
            outbound_key = self.derive_key(b"sender")
            outbound_encrypted = self._encrypt_data(outbound_key, outbound_data)
            data = {"message": hexlify(outbound_encrypted).decode("ascii")}
            return self.post(self.url("post", "data"), data)
        d.addCallback(lambda j: self.get_msgs(j["messages"], "poll", "data"))
        def _got_data(msgs):
            inbound_encrypted = unhexlify(msgs[0].encode("ascii"))
            inbound_key = self.derive_key(b"receiver")
            try:
                inbound_data = self._decrypt_data(inbound_key,
                                                  inbound_encrypted)
                return inbound_data
            except CryptoError:
                raise WrongPasswordError
        d.addCallback(_got_data)
        d.addBoth(self._deallocate)
        return d


    def serialize(self):
        assert self.code is not None
        data = {
            "appid": self.appid,
            "payload_for_them": self.payload_for_them.encode("hex"),
            "relay": self.relay,
            "code": self.code,
            "wormhole": json.loads(self.sp.serialize()),
            "msg1": self.msg1.encode("hex"),
        }
        return json.dumps(data)

    @classmethod
    def from_serialized(klass, data):
        d = json.loads(data)
        self = klass(str(d["appid"]), d["payload_for_them"].decode("hex"),
                     str(d["relay"]))
        self.code = str(d["code"])
        self.sp = SPAKE2_Symmetric.from_serialized(json.dumps(d["wormhole"]))
        self.msg1 = d["msg1"].decode("hex")
        return self
