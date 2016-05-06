from __future__ import print_function
import os, sys, time, re, requests, json, unicodedata
from six.moves.urllib_parse import urlencode
from binascii import hexlify, unhexlify
from spake2 import SPAKE2_Symmetric
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from .eventsource import EventSourceFollower
from .. import __version__
from .. import codes
from ..errors import ServerError, Timeout, WrongPasswordError, UsageError
from ..timing import DebugTiming
from hkdf import Hkdf
from ..channel_monitor import monitor

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

SECOND = 1
MINUTE = 60*SECOND

CONFMSG_NONCE_LENGTH = 128//8
CONFMSG_MAC_LENGTH = 256//8
def make_confmsg(confkey, nonce):
    return nonce+HKDF(confkey, CONFMSG_MAC_LENGTH, nonce)

def to_bytes(u):
    return unicodedata.normalize("NFC", u).encode("utf-8")

class Channel:
    def __init__(self, relay_url, appid, channelid, side, handle_welcome,
                 wait, timeout, timing):
        self._relay_url = relay_url
        self._appid = appid
        self._channelid = channelid
        self._side = side
        self._handle_welcome = handle_welcome
        self._messages = set() # (phase,body) , body is bytes
        self._sent_messages = set() # (phase,body)
        self._started = time.time()
        self._wait = wait
        self._timeout = timeout
        self._timing = timing

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
        payload = {"appid": self._appid,
                   "channelid": self._channelid,
                   "side": self._side,
                   "phase": phase,
                   "body": hexlify(msg).decode("ascii")}
        data = json.dumps(payload).encode("utf-8")
        with self._timing.add("send %s" % phase):
            r = requests.post(self._relay_url+"add", data=data,
                              timeout=self._timeout)
            r.raise_for_status()
            resp = r.json()
        if "welcome" in resp:
            self._handle_welcome(resp["welcome"])
        self._add_inbound_messages(resp["messages"])

    def get_first_of(self, phases):
        if not isinstance(phases, (list, set)): raise TypeError(type(phases))
        for phase in phases:
            if not isinstance(phase, type(u"")): raise TypeError(type(phase))

        # For now, server errors cause the client to fail. TODO: don't. This
        # will require changing the client to re-post messages when the
        # server comes back up.

        # fire with a bytestring of the first message for any 'phase' that
        # wasn't one of our own messages. It will either come from
        # previously-received messages, or from an EventSource that we attach
        # to the corresponding URL
        with self._timing.add("get %s" % "/".join(sorted(phases))):
            phase_and_body = self._find_inbound_message(phases)
            while phase_and_body is None:
                remaining = self._started + self._timeout - time.time()
                if remaining < 0:
                    raise Timeout
                queryargs = urlencode([("appid", self._appid),
                                       ("channelid", self._channelid)])
                f = EventSourceFollower(self._relay_url+"watch?%s" % queryargs,
                                        remaining)
                # we loop here until the connection is lost, or we see the
                # message we want
                for (eventtype, line) in f.iter_events():
                    if eventtype == "welcome":
                        self._handle_welcome(json.loads(line))
                    if eventtype == "message":
                        data = json.loads(line)
                        self._add_inbound_messages([data])
                        phase_and_body = self._find_inbound_message(phases)
                        if phase_and_body:
                            f.close()
                            break
                if not phase_and_body:
                    time.sleep(self._wait)
        return phase_and_body

    def get(self, phase):
        (got_phase, body) = self.get_first_of([phase])
        assert got_phase == phase
        return body

    def deallocate(self, mood=None):
        # only try once, no retries
        data = json.dumps({"appid": self._appid,
                           "channelid": self._channelid,
                           "side": self._side,
                           "mood": mood}).encode("utf-8")
        try:
            # ignore POST failure, don't call r.raise_for_status(), set a
            # short timeout and ignore failures
            with self._timing.add("close"):
                r = requests.post(self._relay_url+"deallocate", data=data,
                                  timeout=5)
                r.json()
        except requests.exceptions.RequestException:
            pass

class ChannelManager:
    def __init__(self, relay_url, appid, side, handle_welcome, timing=None,
                 wait=0.5*SECOND, timeout=3*MINUTE):
        self._relay_url = relay_url
        self._appid = appid
        self._side = side
        self._handle_welcome = handle_welcome
        self._timing = timing or DebugTiming()
        self._wait = wait
        self._timeout = timeout

    def list_channels(self):
        queryargs = urlencode([("appid", self._appid)])
        with self._timing.add("list"):
            r = requests.get(self._relay_url+"list?%s" % queryargs,
                             timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
            if "welcome" in data:
                self._handle_welcome(data["welcome"])
        channelids = data["channelids"]
        return channelids

    def allocate(self):
        data = json.dumps({"appid": self._appid,
                           "side": self._side}).encode("utf-8")
        with self._timing.add("allocate"):
            r = requests.post(self._relay_url+"allocate", data=data,
                              timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
            if "welcome" in data:
                self._handle_welcome(data["welcome"])
        channelid = data["channelid"]
        return channelid

    def connect(self, channelid):
        return Channel(self._relay_url, self._appid, channelid, self._side,
                       self._handle_welcome, self._wait, self._timeout,
                       self._timing)

def close_on_error(f): # method decorator
    # Clients report certain errors as "moods", so the server can make a
    # rough count failed connections (due to mismatched passwords, attacks,
    # or timeouts). We don't report precondition failures, as those are the
    # responsibility/fault of the local application code. We count
    # non-precondition errors in case they represent server-side problems.
    def _f(self, *args, **kwargs):
        try:
            return f(self, *args, **kwargs)
        except Timeout:
            self.close(u"lonely")
            raise
        except WrongPasswordError:
            self.close(u"scary")
            raise
        except (TypeError, UsageError):
            # preconditions don't warrant _close_with_error()
            raise
        except:
            self.close(u"errory")
            raise
    return _f

class Wormhole:
    motd_displayed = False
    version_warning_displayed = False
    _send_confirm = True

    def __init__(self, appid, relay_url, wait=0.5*SECOND, timeout=3*MINUTE,
                 timing=None):
        if not isinstance(appid, type(u"")): raise TypeError(type(appid))
        if not isinstance(relay_url, type(u"")):
            raise TypeError(type(relay_url))
        if not relay_url.endswith(u"/"): raise UsageError
        self._appid = appid
        self._relay_url = relay_url
        self._wait = wait
        self._timeout = timeout
        self._timing = timing or DebugTiming()
        side = hexlify(os.urandom(5)).decode("ascii")
        self._channel_manager = ChannelManager(relay_url, appid, side,
                                               self.handle_welcome,
                                               self._timing,
                                               self._wait, self._timeout)
        self._channel = None
        self.code = None
        self.key = None
        self.verifier = None
        self._sent_data = set() # phases
        self._got_data = set()
        self._got_confirmation = False
        self._closed = False
        self._timing_started = self._timing.add("wormhole")

    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def handle_welcome(self, welcome):
        if ("motd" in welcome and
            not self.motd_displayed):
            motd_lines = welcome["motd"].splitlines()
            motd_formatted = "\n ".join(motd_lines)
            print("Server (at %s) says:\n %s" % (self._relay_url, motd_formatted),
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
            raise ServerError(welcome["error"], self._relay_url)

    def get_code(self, code_length=2):
        if self.code is not None: raise UsageError
        channelid = self._channel_manager.allocate()
        code = codes.make_code(channelid, code_length)
        assert isinstance(code, type(u"")), type(code)
        self._set_code_and_channelid(code)
        self._start()
        return code

    def input_code(self, prompt="Enter wormhole code: ", code_length=2):
        lister = self._channel_manager.list_channels
        # fetch the list of channels ahead of time, to give us a chance to
        # discover the welcome message (and warn the user about an obsolete
        # client)
        initial_channelids = lister()
        with self._timing.add("input code", waiting="user"):
            code = codes.input_code_with_completion(prompt,
                                                    initial_channelids, lister,
                                                    code_length)
        return code

    def set_code(self, code): # used for human-made pre-generated codes
        if not isinstance(code, type(u"")): raise TypeError(type(code))
        if self.code is not None: raise UsageError
        self._set_code_and_channelid(code)
        self._start()

    def _set_code_and_channelid(self, code):
        if self.code is not None: raise UsageError
        self._timing.add("code established")
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

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        if not isinstance(purpose, type(u"")): raise TypeError(type(purpose))
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
        if not self.key:
            self._channel.send(u"pake", self.msg1)
            pake_msg = self._channel.get(u"pake")

            self.key = self.sp.finish(pake_msg)
            self.verifier = self.derive_key(u"wormhole:verifier")
            self._timing.add("key established")

            if not self._send_confirm:
                return
            confkey = self.derive_key(u"wormhole:confirmation")
            nonce = os.urandom(CONFMSG_NONCE_LENGTH)
            confmsg = make_confmsg(confkey, nonce)
            self._channel.send(u"_confirm", confmsg)

    @close_on_error
    def get_verifier(self):
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        self._get_key()
        return self.verifier

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
        with self._timing.add("API send data", phase=phase):
            # Without predefined roles, we can't derive predictably unique
            # keys for each side, so we use the same key for both. We use
            # random nonces to keep the messages distinct, and the Channel
            # automatically ignores reflections.
            self._sent_data.add(phase)
            self._get_key()
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            outbound_encrypted = self._encrypt_data(data_key, outbound_data)
            self._channel.send(phase, outbound_encrypted)

    @close_on_error
    def get_data(self, phase=u"data"):
        if not isinstance(phase, type(u"")): raise TypeError(type(phase))
        if phase in self._got_data: raise UsageError # only call this once
        if phase.startswith(u"_"): raise UsageError # reserved for internals
        if self._closed: raise UsageError
        if self.code is None: raise UsageError
        if self._channel is None: raise UsageError
        with self._timing.add("API get data", phase=phase):
            self._got_data.add(phase)
            self._get_key()
            phases = []
            if not self._got_confirmation:
                phases.append(u"_confirm")
            phases.append(phase)
            (got_phase, body) = self._channel.get_first_of(phases)
            if got_phase == u"_confirm":
                confkey = self.derive_key(u"wormhole:confirmation")
                nonce = body[:CONFMSG_NONCE_LENGTH]
                if body != make_confmsg(confkey, nonce):
                    raise WrongPasswordError
                self._got_confirmation = True
                (got_phase, body) = self._channel.get_first_of([phase])
            assert got_phase == phase
        try:
            data_key = self.derive_key(u"wormhole:phase:%s" % phase)
            inbound_data = self._decrypt_data(data_key, body)
            return inbound_data
        except CryptoError:
            raise WrongPasswordError

    def close(self, mood=u"happy"):
        if not isinstance(mood, (type(None), type(u""))):
            raise TypeError(type(mood))
        self._closed = True
        if self._channel:
            self._timing_started.finish(mood=mood)
            c, self._channel = self._channel, None
            monitor.close(c)
            c.deallocate(mood)
