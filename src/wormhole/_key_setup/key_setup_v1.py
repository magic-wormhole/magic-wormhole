from zope.interface import implementer

from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, derive_phase_key, HKDF,
                    encrypt_data, decrypt_data, CryptoError)
from ..errors import CrowdedError, WrongPasswordError, CausalityError
from . import ikeysetup
from .spake2_helper import SPAKE2_Helper
from .hash_transcript import hash_transcript
from .next_phase import next_phase

# "v1" is SPAKE-2 -only, like v0, but exercises the new
# version-negotiation protocol. It also hashes the full transcript
# into the session key, including the version offers, which protects
# against a downgrade attack (where both Alice and Bob can do v1, but
# the attacker modifies their offers mid-flight, so they negotiate an
# older version). v0 didn't do this, so clients who are willing to
# speak v0 are still vulnerable to the downgrade attack, but once v1
# support is common enough we can make v0 opt-in, and protect against
# downgrade except where explicitly enabled with a "--enable-v0"
# command-line argument.

@implementer(ikeysetup.IKeySetup)
class KeySetup_V1:
    VERSION = "v1"

    def __init__(self, side, appid, app_versions, timing, spake2_helper=None):
        self._side = side
        self._appid = appid
        self._app_versions = app_versions
        self._timing = timing
        if not spake2_helper:
            spake2_helper = SPAKE2_Helper(appid)
        assert isinstance(spake2_helper, SPAKE2_Helper)
        self._sph = spake2_helper
        self._spake2_key = None
        self._key = None

        self._started = False
        self._done = False
        self._error = None

        self._their_side = None
        self._inbound_messages = dict()
        self._want_transcript = False
        self._wanted = None
        self._outputs: list[ikeysetup.KeySetupAction] = []

        self._have_pake = False

    # start() might supply the outbound pake-0 (if we're informed, or
    # optimistic and lucky), or the pake-1 (if we're optimistic and
    # unlucky)
    def start(self, code, _side):
        # this protocol doesn't use the peer's side until later
        assert not self._started, "start() may only be called once)"
        msg1 = self._sph.start(code)
        self._wanted = "pake"
        self._process()
        self._started = True
        return {"pake_v1": bytes_to_hexstr(msg1)}

    def input(self, side, phase, body):
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        assert not self._done
        if self._their_side is None:
            self._their_side = side
        if self._their_side != side:
            self._error = self._error or CrowdedError()
        if phase == "version" and not self._started:
            self._error = self._error or CausalityError()
        if self._error:
            raise self._error
        assert phase not in self._inbound_messages
        self._inbound_messages[phase] = (side, body)
        self._process_inbound()

    def _process_inbound(self):
        while True:
            if not self._wanted:
                break
            if self._want_transcript:
                break
            if self._wanted in self._inbound_messages:
                # could pop() instead, Mailbox ought to dedup
                (s, b) = self._inbound_messages[self._wanted]
                self._wanted = self._process_next(s, self._wanted, b)

    # TODO it'd be cool if we could deliver the transcript like a
    # phase, and have it processed here in _process_next instead of a
    # separate function. Maybe have a distinct TRANSCRIPT symbol.

    def _process_next(self, side, phase, body):
        assert not self._want_transcript
        self._wanted = next_phase(phase)
        payload = bytes_to_dict(body)
        if not self._have_pake:
            # we start in "wanting pake_v1" mode, where all messages
            # go into the transcript
            self._output(ikeysetup.AddToTranscript(side, phase, body))
            if "pake_v1" in payload:
                # receiving a phase with "pake_v1" lets us build the
                # key and go into "confirming" mode
                msg2 = hexstr_to_bytes(payload["pake_v1"])
                assert isinstance(msg2, bytes)
                with self._timing.add("pake2", waiting="crypto"):
                    self._spake2_key = self._sph.finish(msg2)
                # confirming mode means we want the transcript
                self._output(ikeysetup.GetTranscript())
                self._want_transcript = True
            # else keep waiting
        elif self._wanted != "version":
            # we're waiting for the pre-version
            assert self._key
            if "key_setup_version" in payload:
                their_version = payload["key_setup_version"]
                if their_version != self.VERSION:
                    msg = ("version mismatch: me=%s, them=%s" %
                           (self.VERSION, their_version))
                    self._error = WrongPasswordError(msg)
                    raise self._error
                self._wanted = "version"
        else:
            assert self._key
            assert self._wanted == "version"
            self._process_version(side, phase, body)

    def transcriptIs(self, transcript):
        assert self._want_transcript
        assert self._spake2_key
        self._want_transcript = False
        t_hash = hash_transcript(self.VERSION, transcript)
        skm = self._spake2_key + t_hash
        tag = b"magic-wormhole key setup"
        self._key = HKDF(skm, 32, CTXinfo=tag)
        self._output(ikeysetup.HaveAllegedKey())
        # send pre-version
        preversion = { "key_setup_version": self.VERSION }
        preversion_body = dict_to_bytes(preversion)
        self._output(ikeysetup.Send(preversion_body, False))
        # send VERSION
        data_key = derive_phase_key(self._key, self._side, "version")
        plaintext = dict_to_bytes(self._app_versions)
        encrypted = encrypt_data(data_key, plaintext)
        self._output(ikeysetup.SendVersion(encrypted))
        # next self._wanted should have pre-version

    def _process_version(self, side, phase, body):
        assert self._key
        assert phase == "version"
        data_key = derive_phase_key(self._key, side, phase)
        try:
            plaintext = decrypt_data(data_key, body)
        except CryptoError:
            self._error = WrongPasswordError()
            raise self._error
        self._done = True
        self._output(ikeysetup.Done(self._key, plaintext))

    def _output(self, action):
        self._outputs.append(action)

    def output(self):
        if self._error:
            raise self._error
        if self._outputs:
            return self._outputs.pop(0)
        return None
