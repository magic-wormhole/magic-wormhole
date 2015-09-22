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
from ..errors import (ServerError, Timeout, WrongPasswordError,
                      ReflectionAttack, UsageError)
from ..util.hkdf import HKDF

SECOND = 1
MINUTE = 60*SECOND

# relay URLs are:
# GET /list                                         -> {channel-ids: [INT..]}
# POST /allocate/SIDE                               -> {channel-id: INT}
#  these return all messages for CHANNEL-ID= and MSGNUM= but SIDE!= :
# POST /CHANNEL-ID/SIDE/post/MSGNUM  {message: STR} -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/poll/MSGNUM                 -> {messages: [STR..]}
# GET  /CHANNEL-ID/SIDE/poll/MSGNUM (eventsource)   -> STR, STR, ..
# POST /CHANNEL-ID/SIDE/deallocate                  -> waiting | deleted

class Wormhole:
    motd_displayed = False
    version_warning_displayed = False

    def __init__(self, appid, relay):
        self.appid = appid
        self.relay = relay
        if not self.relay.endswith("/"): raise UsageError
        self.started = time.time()
        self.wait = 0.5*SECOND
        self.timeout = 3*MINUTE
        self.side = None
        self.code = None
        self.key = None
        self.verifier = None

    def _url(self, verb, msgnum=None):
        url = "%s%d/%s/%s" % (self.relay, self.channel_id, self.side, verb)
        if msgnum is not None:
            url += "/" + msgnum
        return url

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

    def _post_json(self, url, post_json=None):
        # POST to a URL, parsing the response as JSON. Optionally include a
        # JSON request body.
        data = None
        if post_json:
            data = json.dumps(post_json).encode("utf-8")
        r = requests.post(url, data=data)
        r.raise_for_status()
        return r.json()

    def _allocate_channel(self):
        r = requests.post(self.relay + "allocate/%s" % self.side)
        r.raise_for_status()
        data = r.json()
        if "welcome" in data:
            self.handle_welcome(data["welcome"])
        channel_id = data["channel-id"]
        return channel_id

    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        self.side = hexlify(os.urandom(5))
        channel_id = self._allocate_channel() # allocate channel
        code = codes.make_code(channel_id, code_length)
        self._set_code_and_channel_id(code)
        self._start()
        return code

    def list_channels(self):
        r = requests.get(self.relay + "list")
        r.raise_for_status()
        channel_ids = r.json()["channel-ids"]
        return channel_ids

    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        code = codes.input_code_with_completion(prompt, self.list_channels,
                                                code_length)
        return code

    def set_code(self, code): # used for human-made pre-generated codes
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
                                   idSymmetric=self.appid)
        self.msg1 = self.sp.start()

    def _post_message(self, url, msg):
        # TODO: retry on failure, with exponential backoff. We're guarding
        # against the rendezvous server being temporarily offline.
        if not isinstance(msg, type(b"")): raise UsageError(type(msg))
        resp = self._post_json(url, {"message": hexlify(msg).decode("ascii")})
        return resp["messages"] # other_msgs

    def _get_message(self, old_msgs, verb, msgnum):
        # For now, server errors cause the client to fail. TODO: don't. This
        # will require changing the client to re-post messages when the
        # server comes back up.

        # fire with a bytestring of the first message that matches
        # verb/msgnum, which either came from old_msgs, or from an
        # EventSource that we attached to the corresponding URL
        msgs = old_msgs
        while not msgs:
            remaining = self.started + self.timeout - time.time()
            if remaining < 0:
                raise Timeout
            #time.sleep(self.wait)
            f = EventSourceFollower(self._url(verb, msgnum), remaining)
            for (eventtype, data) in f.iter_events():
                if eventtype == "welcome":
                    self.handle_welcome(json.loads(data))
                if eventtype == "message":
                    msgs = [json.loads(data)["message"]]
                    break
            f.close()
        return unhexlify(msgs[0].encode("ascii"))

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(b"")): raise UsageError
        return HKDF(self.key, length, CTXinfo=purpose)

    def _encrypt_data(self, key, data):
        if len(key) != SecretBox.KEY_SIZE: raise UsageError
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(data, nonce)

    def _decrypt_data(self, key, encrypted):
        if len(key) != SecretBox.KEY_SIZE: raise UsageError
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data


    def _get_key(self):
        if not self.key:
            old_msgs = self._post_message(self._url("post", "pake"), self.msg1)
            pake_msg = self._get_message(old_msgs, "poll", "pake")
            self.key = self.sp.finish(pake_msg)
            self.verifier = self.derive_key(self.appid+b":Verifier")

    def get_verifier(self):
        if self.code is None: raise UsageError
        if self.channel_id is None: raise UsageError
        self._get_key()
        return self.verifier

    def get_data(self, outbound_data):
        # only call this once
        if self.code is None: raise UsageError
        if self.channel_id is None: raise UsageError
        try:
            self._get_key()
            return self._get_data2(outbound_data)
        finally:
            self._deallocate()

    def _get_data2(self, outbound_data):
        # Without predefined roles, we can't derive predictably unique keys
        # for each side, so we use the same key for both. We use random
        # nonces to keep the messages distinct, and check for reflection.
        data_key = self.derive_key(b"data-key")

        outbound_encrypted = self._encrypt_data(data_key, outbound_data)
        msgs = self._post_message(self._url("post", "data"), outbound_encrypted)

        inbound_encrypted = self._get_message(msgs, "poll", "data")
        if inbound_encrypted == outbound_encrypted:
            raise ReflectionAttack
        try:
            inbound_data = self._decrypt_data(data_key, inbound_encrypted)
            return inbound_data
        except CryptoError:
            raise WrongPasswordError

    def _deallocate(self):
        # only try once, no retries
        requests.post(self._url("deallocate"))
        # ignore POST failure, don't call r.raise_for_status()
