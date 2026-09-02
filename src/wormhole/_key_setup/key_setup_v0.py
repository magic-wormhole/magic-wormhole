from zope.interface import implementer

from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, derive_phase_key,
                    encrypt_data, decrypt_data, CryptoError)
from ..errors import CrowdedError, WrongPasswordError, CausalityError
from .ikeysetup import IKeySetup, Send, HaveAllegedKey, Done, KeySetupAction
from .spake2_helper import SPAKE2_Helper

# This is the retroactively-named "v0" key-setup protocol: the initial
# one used by all versions of magic-wormhole, at least through the
# 0.24.0 release. We implement here as an IKeySetup so that future
# versions of the client can fall back to it when their peer can't do
# something better.

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

        self._started = False
        self._done = False
        self._error = None

        self._their_side = None
        self._inbound_messages = dict()
        self._wanted = None
        self._outputs: list[KeySetupAction] = []

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
        self._process()

    # v0 does not use transcriptIs(), sadly

    def _process(self):
        while self._wanted and self._wanted in self._inbound_messages:
            (s, b) = self._inbound_messages[self._wanted]
            if self._wanted == "pake":
                self._wanted = self._process_pake(s, self._wanted, b)
            elif self._wanted == "version":
                self._wanted = self._process_version(s, self._wanted, b)
            else:
                raise AssertionError("unhandled phase %s" % self._wanted)

    def _process_pake(self, side, phase, body):
        payload = bytes_to_dict(body)
        msg2 = hexstr_to_bytes(payload["pake_v1"])
        assert isinstance(msg2, bytes)
        with self._timing.add("pake2", waiting="crypto"):
            key = self._sph.finish(msg2)
        self._key = key

        self._outputs.append(HaveAllegedKey(key))
        data_key = derive_phase_key(self._key, self._side, "version")
        plaintext = dict_to_bytes(self._app_versions)
        encrypted = encrypt_data(data_key, plaintext)
        self._outputs.append(Send("version", encrypted))
        return "version"

    def _process_version(self, side, phase, body):
        assert self._key
        data_key = derive_phase_key(self._key, side, phase)
        try:
            plaintext = decrypt_data(data_key, body)
        except CryptoError:
            self._error = WrongPasswordError()
            raise self._error
        self._done = True
        self._outputs.append(Done(self._key, plaintext))
        return None

    def output(self):
        if self._error:
            raise self._error
        if self._outputs:
            return self._outputs.pop(0)
        return None
