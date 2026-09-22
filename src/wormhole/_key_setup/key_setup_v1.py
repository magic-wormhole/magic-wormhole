from attrs import frozen
from zope.interface import implementer

from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, derive_phase_key, HKDF,
                    encrypt_data, decrypt_data, CryptoError)
from ..errors import CrowdedError, WrongPasswordError
from . import ikeysetup
from .ikeysetup import MessageTuple, NextKeySetupInput
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

# states
@frozen
class Init:
    pass
@frozen
class StartedEarly: # waiting for outbound PAKE-0
    pass
@frozen
class WantPAKE: # -> VerifyingOurVersion
    wanted: str
@frozen
class VerifyingOurVersion: # -> VerifyingKey
    key: bytes
    wanted: str
@frozen
class VerifyingKey: # -> Done
    key: bytes
@frozen
class Done:
    pass



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

        self._error = None

        self._their_side = None
        self._transcript: list[MessageTuple] = []
        self._next_outbound_phase = "pake" # PAKE-0
        self._outputs: list[ikeysetup.KeySetupAction] = []

        self._state = Init()

    def start_pake0(self, code: str, their_side: str | None) -> dict:
        # this protocol doesn't use the peer's side until later
        assert self._state == Init()
        msg1 = self._sph.start(code)
        self._state = StartedEarly()
        return {"pake_v1": bytes_to_hexstr(msg1)}

    def submit_outbound_pake0(self, pake0mt: MessageTuple):
        assert self._state == StartedEarly()
        assert isinstance(pake0mt, tuple)
        self._transcript.append(pake0mt)
        self._next_outbound_phase = "pake-1" # for pre-version
        wanted = "pake"
        self._state = WantPAKE(wanted)
        return wanted

    def start_pake1(self, code: str, their_side: str, pake0mt: MessageTuple) -> NextKeySetupInput:
        # this could only really be called in a v0-disallowed client
        # that is also v1-pessimistic
        assert self._state == Init()
        assert isinstance(pake0mt, tuple)
        self._transcript.append(pake0mt)
        msg1 = self._sph.start(code)
        components = {"pake_v1": bytes_to_hexstr(msg1)}
        body = dict_to_bytes(components)
        send = ikeysetup.Send(self._side, "pake-1", body)
        self._next_outbound_phase = "pake-2" # for pre-version
        wanted = "pake"
        self._state = WantPAKE(wanted)
        return (wanted, [send])

    def input(self, side: str, phase: str, body: bytes) -> NextKeySetupInput:
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        if self._their_side is None:
            self._their_side = side
        if self._their_side != side:
            self._error = self._error or CrowdedError()
        if self._error:
            raise self._error
        actions = False
        next_wanted = False
        match self._state:
            case Init():
                raise ValueError("input() before start")
            case StartedEarly():
                raise ValueError("input() before submit_outbound_pake0")
            case WantPAKE(wanted):
                assert phase == wanted
                self._transcript.append((side, phase, body))
                payload = bytes_to_dict(body)
                if "pake_v1" in payload:
                    # receiving a phase with "pake_v1" lets us build the
                    # key and go into "confirming" mode
                    msg2 = hexstr_to_bytes(payload["pake_v1"])
                    assert isinstance(msg2, bytes)
                    with self._timing.add("pake2", waiting="crypto"):
                        spake2_key = self._sph.finish(msg2)
                    key = self._compute_session_key(spake2_key)
                    have_alleged_key = ikeysetup.HaveAllegedKey()
                    s_pre_version = self._send_pre_version(key)
                    s_version = self._send_version(key)
                    actions = [have_alleged_key, s_pre_version, s_version]
                    next_wanted = next_phase(phase)
                    self._state = VerifyingOurVersion(key, next_wanted)
                else:
                    # keep waiting, WEIRD
                    actions = []
                    next_wanted = next_phase(phase)
                    self._state = WantPAKE(next_wanted)
            case VerifyingOurVersion(key, wanted):
                assert phase == wanted
                # *not* added to transcript
                payload = bytes_to_dict(body)
                if "our_key_setup_version" in payload:
                    their_version = payload["our_key_setup_version"]
                    if their_version != self.VERSION:
                        msg = ("version mismatch: me=%s, them=%s" %
                               (self.VERSION, their_version))
                        self._error = WrongPasswordError(msg)
                        raise self._error
                    actions = []
                    next_wanted = "version"
                    self._state = VerifyingKey(key)
                else:
                    # keep waiting, WEIRD
                    actions = []
                    next_wanted = next_phase(phase)
                    self._state = VerifyingOurVersion(key, next_wanted)
            case VerifyingKey(key):
                assert phase == "version"
                data_key = derive_phase_key(key, side, phase)
                try:
                    plaintext = decrypt_data(data_key, body)
                except CryptoError:
                    self._error = WrongPasswordError("invalid KCM")
                    raise self._error
                next_wanted = None
                actions = [ikeysetup.Done(key, plaintext)]
                self._state = Done()
            case _:
                raise ValueError("bad state")
        assert isinstance(actions, list)
        assert next_wanted != False
        return actions, next_wanted

    def _compute_session_key(self, spake2_key):
        t_hash = hash_transcript(self.VERSION, self._transcript)
        skm = spake2_key + t_hash
        tag = b"magic-wormhole key setup"
        key = HKDF(skm, 32, CTXinfo=tag)
        return key

    def _send_pre_version(self, key):
        preversion = { "our_key_setup_version": self.VERSION }
        preversion_body = dict_to_bytes(preversion)
        phase = self._next_outbound_phase
        self._next_outbound_phase = next_phase(phase)
        return ikeysetup.Send(self._side, phase, preversion_body)

    def _send_version(self, key):
        data_key = derive_phase_key(key, self._side, "version")
        plaintext = dict_to_bytes(self._app_versions)
        encrypted = encrypt_data(data_key, plaintext)
        return ikeysetup.Send(self._side, "version", encrypted)
