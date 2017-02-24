from __future__ import print_function, absolute_import, unicode_literals
from hashlib import sha256
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides, instance_of
from spake2 import SPAKE2_Symmetric
from hkdf import Hkdf
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from automat import MethodicalMachine
from .util import (to_bytes, bytes_to_hexstr, hexstr_to_bytes,
                   bytes_to_dict, dict_to_bytes)
from . import _interfaces
CryptoError
__all__ = ["derive_key", "derive_phase_key", "CryptoError",
           "Key"]

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

def derive_key(key, purpose, length=SecretBox.KEY_SIZE):
    if not isinstance(key, type(b"")): raise TypeError(type(key))
    if not isinstance(purpose, type(b"")): raise TypeError(type(purpose))
    if not isinstance(length, int): raise TypeError(type(length))
    return HKDF(key, length, CTXinfo=purpose)

def derive_phase_key(side, phase):
    assert isinstance(side, type("")), type(side)
    assert isinstance(phase, type("")), type(phase)
    side_bytes = side.encode("ascii")
    phase_bytes = phase.encode("ascii")
    purpose = (b"wormhole:phase:"
               + sha256(side_bytes).digest()
               + sha256(phase_bytes).digest())
    return derive_key(purpose)

def decrypt_data(key, encrypted):
    assert isinstance(key, type(b"")), type(key)
    assert isinstance(encrypted, type(b"")), type(encrypted)
    assert len(key) == SecretBox.KEY_SIZE, len(key)
    box = SecretBox(key)
    data = box.decrypt(encrypted)
    return data

def encrypt_data(key, plaintext):
    assert isinstance(key, type(b"")), type(key)
    assert isinstance(plaintext, type(b"")), type(plaintext)
    assert len(key) == SecretBox.KEY_SIZE, len(key)
    box = SecretBox(key)
    nonce = utils.random(SecretBox.NONCE_SIZE)
    return box.encrypt(plaintext, nonce)

@attrs
@implementer(_interfaces.IKey)
class Key(object):
    _appid = attrib(validator=instance_of(type(u"")))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()

    def wire(self, boss, mailbox, receive):
        self._B = _interfaces.IBoss(boss)
        self._M = _interfaces.IMailbox(mailbox)
        self._R = _interfaces.IReceive(receive)

    @m.state(initial=True)
    def S0_know_nothing(self): pass
    @m.state()
    def S1_know_code(self): pass
    @m.state()
    def S2_know_key(self): pass
    @m.state(terminal=True)
    def S3_scared(self): pass

    # from Boss
    @m.input()
    def got_code(self, code): pass

    # from Ordering
    def got_pake(self, body):
        assert isinstance(body, type(b"")), type(body)
        payload = bytes_to_dict(body)
        if "pake_v1" in payload:
            self.got_pake_good(hexstr_to_bytes(payload["pake_v1"]))
        else:
            self.got_pake_bad()
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
        body = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg1)})
        self._M.add_message("pake", body)

    @m.output()
    def scared(self):
        self._B.scared()
    @m.output()
    def compute_key(self, msg2):
        assert isinstance(msg2, type(b""))
        with self._timing.add("pake2", waiting="crypto"):
            key = self._sp.finish(msg2)
        self._my_versions = {}
        self._M.add_message("version", self._my_versions)
        self._B.got_verifier(derive_key(key, b"wormhole:verifier"))
        self._R.got_key(key)

    S0_know_nothing.upon(got_code, enter=S1_know_code, outputs=[build_pake])
    S1_know_code.upon(got_pake_good, enter=S2_know_key, outputs=[compute_key])
    S1_know_code.upon(got_pake_bad, enter=S3_scared, outputs=[scared])
