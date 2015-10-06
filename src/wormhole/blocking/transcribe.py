from __future__ import print_function
import os, sys, time, re, requests, json
from binascii import hexlify, unhexlify
from spake2 import SPAKE2_Symmetric
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from .eventsource import EventSourceFollower
from .. import __version__
from .. import codes
from ..errors import ServerError, Timeout, WrongPasswordError, UsageError
from ..util.hkdf import HKDF

SECOND = 1
MINUTE = 60*SECOND

# relay URLs are:
#  GET /list                           -> {channelids: [INT..]}
#  POST /allocate {side: SIDE}         -> {channelid: INT}
#   these return all messages (base64) for CID= :
#  POST /CID {side:, phase:, body:}    -> {messages: [{phase:, body:}..]}
#  GET  /CID (no-eventsource)          -> {messages: [{phase:, body:}..]}
#  GET  /CID (eventsource)             -> {phase:, body:}..
#  POST /CID/deallocate {side: SIDE}   -> {status: waiting | deleted}
# all JSON responses include a "welcome:{..}" key

class Channel:
    def __init__(self, relay, channelid, side, handle_welcome):
        self._channel_url = "%s%d" % (relay, channelid)
        self._side = side
        self._handle_welcome = handle_welcome
        self._messages = set() # (phase,body) , body is bytes
        self._sent_messages = set() # (phase,body)
        self._started = time.time()
        self._wait = 0.5*SECOND
        self._timeout = 3*MINUTE

    def _add_inbound_messages(self, messages):
        for msg in messages:
            phase = msg["phase"]
            body = unhexlify(msg["body"].encode("ascii"))
            self._messages.add( (phase, body) )

    def _find_inbound_message(self, phase):
        for (their_phase,body) in self._messages - self._sent_messages:
            if their_phase == phase:
                return body
        return None

    def send(self, phase, msg):
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        if not isinstance(phase, type(u"")): raise UsageError(type(phase))
        if not isinstance(msg, type(b"")): raise UsageError(type(msg))
        self._sent_messages.add( (phase,msg) )
        payload = {"side": self._side,
                   "phase": phase,
                   "body": hexlify(msg).decode("ascii")}
        data = json.dumps(payload).encode("utf-8")
        r = requests.post(self._channel_url, data=data)
        r.raise_for_status()
        resp = r.json()
        self._add_inbound_messages(resp["messages"])

    def get(self, phase):
        if not isinstance(phase, type(u"")): raise UsageError(type(phase))
        # For now, server errors cause the client to fail. TODO: don't. This
        # will require changing the client to re-post messages when the
        # server comes back up.

        # fire with a bytestring of the first message for 'phase' that wasn't
        # one of ours. It will either come from previously-received messages,
        # or from an EventSource that we attach to the corresponding URL
        body = self._find_inbound_message(phase)
        while body is None:
            remaining = self._started + self._timeout - time.time()
            if remaining < 0:
                return Timeout
            f = EventSourceFollower(self._channel_url, remaining)
            # we loop here until the connection is lost, or we see the
            # message we want
            for (eventtype, data) in f.iter_events():
                if eventtype == "welcome":
                    self._handle_welcome(json.loads(data))
                if eventtype == "message":
                    self._add_inbound_messages([json.loads(data)])
                    body = self._find_inbound_message(phase)
                    if body:
                        f.close()
                        break
            if not body:
                time.sleep(self._wait)
        return body

    def deallocate(self):
        # only try once, no retries
        data = json.dumps({"side": self._side}).encode("utf-8")
        requests.post(self._channel_url+"/deallocate", data=data)
        # ignore POST failure, don't call r.raise_for_status()

class ChannelManager:
    def __init__(self, relay, side, handle_welcome):
        self._relay = relay
        self._side = side
        self._handle_welcome = handle_welcome

    def list_channels(self):
        r = requests.get(self._relay + "list")
        r.raise_for_status()
        channelids = r.json()["channelids"]
        return channelids

    def allocate(self):
        data = json.dumps({"side": self._side}).encode("utf-8")
        r = requests.post(self._relay + "allocate", data=data)
        r.raise_for_status()
        data = r.json()
        if "welcome" in data:
            self._handle_welcome(data["welcome"])
        channelid = data["channelid"]
        return channelid

    def connect(self, channelid):
        return Channel(self._relay, channelid, self._side,
                       self._handle_welcome)

class Wormhole:
    motd_displayed = False
    version_warning_displayed = False

    def __init__(self, appid, relay):
        if not isinstance(appid, type(b"")): raise UsageError
        self.appid = appid
        self.relay = relay
        if not self.relay.endswith("/"): raise UsageError
        side = hexlify(os.urandom(5)).decode("ascii")
        self._channel_manager = ChannelManager(relay, side,
                                               self.handle_welcome)
        self.code = None
        self.key = None
        self.verifier = None
        self._sent_data = False
        self._got_data = False
        self._closed = False

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

    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        channelid = self._channel_manager.allocate()
        code = codes.make_code(channelid, code_length)
        assert isinstance(code, str), type(code)
        self._set_code_and_channelid(code)
        self._start()
        return code

    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        lister = self._channel_manager.list_channels
        code = codes.input_code_with_completion(prompt, lister,
                                                code_length)
        return code

    def set_code(self, code): # used for human-made pre-generated codes
        if not isinstance(code, str): raise UsageError
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
        self.channel = self._channel_manager.connect(channelid)

    def _start(self):
        # allocate the rest now too, so it can be serialized
        self.sp = SPAKE2_Symmetric(self.code.encode("ascii"),
                                   idSymmetric=self.appid)
        self.msg1 = self.sp.start()

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(b"")): raise UsageError
        return HKDF(self.key, length, CTXinfo=purpose)

    def _encrypt_data(self, key, data):
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(data, type(b"")), type(data)
        if len(key) != SecretBox.KEY_SIZE: raise UsageError
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(data, nonce)

    def _decrypt_data(self, key, encrypted):
        assert isinstance(key, type(b"")), type(key)
        assert isinstance(encrypted, type(b"")), type(encrypted)
        if len(key) != SecretBox.KEY_SIZE: raise UsageError
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data


    def _get_key(self):
        if not self.key:
            self.channel.send(u"pake", self.msg1)
            pake_msg = self.channel.get(u"pake")
            self.key = self.sp.finish(pake_msg)
            self.verifier = self.derive_key(self.appid+b":Verifier")

    def get_verifier(self):
        if self.code is None: raise UsageError
        if self.channel is None: raise UsageError
        self._get_key()
        return self.verifier

    def send_data(self, outbound_data):
        if self._sent_data: raise UsageError # only call this once
        if not isinstance(outbound_data, type(b"")): raise UsageError
        if self.code is None: raise UsageError
        if self.channel is None: raise UsageError
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and the Channel automatically
        # ignores reflections.
        self._get_key()
        data_key = self.derive_key(b"data-key")
        outbound_encrypted = self._encrypt_data(data_key, outbound_data)
        self.channel.send(u"data", outbound_encrypted)

    def get_data(self):
        if self._got_data: raise UsageError # only call this once
        if self.code is None: raise UsageError
        if self.channel is None: raise UsageError
        self._get_key()
        data_key = self.derive_key(b"data-key")
        inbound_encrypted = self.channel.get(u"data")
        try:
            inbound_data = self._decrypt_data(data_key, inbound_encrypted)
            return inbound_data
        except CryptoError:
            raise WrongPasswordError

    def close(self):
        self.channel.deallocate()
        self._closed = True

    def __del__(self):
        if not self._closed:
            print("Error: a Wormhole instance was not closed", file=sys.stderr)
