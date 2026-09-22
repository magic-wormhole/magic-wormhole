from attrs import frozen
from zope.interface import implementer

from ..util import (bytes_to_dict, bytes_to_hexstr,
                    hexstr_to_bytes, derive_phase_key,
                    dict_to_bytes, encrypt_data,
                    decrypt_data, CryptoError)
from ..errors import CrowdedError, WrongPasswordError, NegotiationError
from . import ikeysetup
from .ikeysetup import IKeySetup, NextKeySetupInput, MessageTuple
from .spake2_helper import SPAKE2_Helper

# This is the retroactively-named "v0" key-setup protocol: the initial
# one used by all versions of magic-wormhole, at least through the
# 0.24.0 release. We implement here as an IKeySetup so that future
# versions of the client can fall back to it when their peer can't do
# something better.

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
class VerifyingKey: # -> Done
    key: bytes
@frozen
class Done:
    pass

@implementer(IKeySetup)
class KeySetup_V0:
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
        self._state = Init()

    def start_pake0(self, code: str, their_side: str | None) -> dict:
        assert self._state == Init()
        msg1 = self._sph.start(code)
        self._state = StartedEarly()
        return {"pake_v1": bytes_to_hexstr(msg1)}

    def submit_outbound_pake0(self, pake0mt: MessageTuple):
        # v0 doesn't use a transcript, the PAKE-0 is ignored
        wanted = "pake"
        self._state = WantPAKE(wanted)
        return wanted

    def start_pake1(self, code: str, their_side: str, pake0mt: MessageTuple) -> NextKeySetupInput:
        raise ValueError("v0 cannot be started late")

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
                payload = bytes_to_dict(body)
                if "pake_v1" not in payload:
                    raise NegotiationError("PAKE-0 missing 'pake_v1'")
                # receiving a phase with "pake_v1" lets us build the
                # key and go into "confirming" mode
                msg2 = hexstr_to_bytes(payload["pake_v1"])
                assert isinstance(msg2, bytes)
                with self._timing.add("pake2", waiting="crypto"):
                    spake2_key = self._sph.finish(msg2)
                key = spake2_key # no transcript
                have_alleged_key = ikeysetup.HaveAllegedKey()
                s_version = self._send_version(key)
                actions = [have_alleged_key, s_version]
                next_wanted = "version"
                self._state = VerifyingKey(key)
            case VerifyingKey(key):
                assert phase == "version"
                data_key = derive_phase_key(key, side, phase)
                try:
                    plaintext = decrypt_data(data_key, body)
                except CryptoError:
                    self._error = WrongPasswordError()
                    raise self._error
                next_wanted = None
                actions = [ikeysetup.Done(key, plaintext)]
                self._state = Done()
            case _:
                raise ValueError("bad state")
        assert isinstance(actions, list)
        assert next_wanted != False
        return actions, next_wanted

    def _send_version(self, key):
        data_key = derive_phase_key(key, self._side, "version")
        plaintext = dict_to_bytes(self._app_versions)
        encrypted = encrypt_data(data_key, plaintext)
        return ikeysetup.Send(self._side, "version", encrypted)
