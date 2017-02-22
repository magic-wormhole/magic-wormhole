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

class SendMachine(object):
    m = MethodicalMachine()
    def __init__(self, timing):
        self._timing = timing
    def set_mailbox(self, mailbox):
        self._mailbox = mailbox

    @m.state(initial=True)
    def S0_no_key(self): pass
    @m.state()
    def S1_verified_key(self): pass

    def got_pake(self, payload):
        if "pake_v1" in payload:
            self.got_pake_good(hexstr_to_bytes(payload["pake_v1"]))
        else:
            self.got_pake_bad()

    @m.input()
    def got_verified_key(self, key): pass
    @m.input()
    def send(self, phase, payload): pass

    @m.output()
    def queue(self, phase, payload):
        self._queue.append((phase, payload))
    @m.output()
    def record_key(self, key):
        self._key = key
    @m.output()
    def drain(self, key):
        del key
        for (phase, payload) in self._queue:
            self._encrypt_and_send(phase, payload)
    @m.output()
    def deliver(self, phase, payload):
        self._encrypt_and_send(phase, payload)

    def _encrypt_and_send(self, phase, payload):
        data_key = self._derive_phase_key(self._side, phase)
        encrypted = self._encrypt_data(data_key, plaintext)
        self._mailbox.add_message(phase, encrypted)

    S0_no_key.upon(send, enter=S0_no_key, outputs=[queue])
    S0_no_key.upon(got_verified_key, enter=S1_verified_key,
                   outputs=[record_key, drain])
    S1_verified_key.upon(send, enter=S1_verified_key, outputs=[deliver])
