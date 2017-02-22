from automat import MethodicalMachine
from spake2 import SPAKE2_Symmetric
from hkdf import Hkdf
from nacl.secret import SecretBox
from .util import (to_bytes, bytes_to_hexstr, hexstr_to_bytes)

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

def derive_key(key, purpose, length=SecretBox.KEY_SIZE):
    if not isinstance(key, type(b"")): raise TypeError(type(key))
    if not isinstance(purpose, type(b"")): raise TypeError(type(purpose))
    if not isinstance(length, int): raise TypeError(type(length))
    return HKDF(key, length, CTXinfo=purpose)

class KeyMachine(object):
    m = MethodicalMachine()
    def __init__(self, wormhole, timing):
        self._wormhole = wormhole
        self._timing = timing
    def set_mailbox(self, mailbox):
        self._mailbox = mailbox
    def set_receive(self, receive):
        self._receive = receive

    @m.state(initial=True)
    def S0_know_nothing(self): pass
    @m.state()
    def S1_know_code(self): pass
    @m.state()
    def S2_know_key(self): pass
    @m.state()
    def S3_scared(self): pass

    def got_pake(self, payload):
        if "pake_v1" in payload:
            self.got_pake_good(hexstr_to_bytes(payload["pake_v1"]))
        else:
            self.got_pake_bad()

    @m.input()
    def set_code(self, code): pass
    @m.input()
    def got_pake_good(self, msg2): pass
    @m.input()
    def got_pake_bad(self): pass

    @m.output()
    def build_pake(self, code):
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(to_bytes(code),
                                        idSymmetric=to_bytes(self._appid))
            msg1 = self._sp.start()
        self._mailbox.add_message("pake", {"pake_v1": bytes_to_hexstr(msg1)})

    @m.output()
    def scared(self):
        self._wormhole.scared()
    @m.output()
    def compute_key(self, msg2):
        assert isinstance(msg2, type(b""))
        with self._timing.add("pake2", waiting="crypto"):
            key = self._sp.finish(msg2)
        self._my_versions = {}
        self._mailbox.add_message("version", self._my_versions)
        self._wormhole.got_verifier(derive_key(key, b"wormhole:verifier"))
        self._receive.got_key(key)

    S0_know_nothing.upon(set_code, enter=S1_know_code, outputs=[build_pake])
    S1_know_code.upon(got_pake_good, enter=S2_know_key, outputs=[compute_key])
    S1_know_code.upon(got_pake_bad, enter=S3_scared, outputs=[scared])
