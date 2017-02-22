from zope.interface import implementer
from automat import MethodicalMachine
from . import _interfaces
from ._key import derive_phase_key, decrypt_data, CryptoError

@implementer(_interfaces.IReceive)
class Receive(object):
    m = MethodicalMachine()
    def __init__(self, side, timing):
        self._side = side
        self._timing = timing
        self._key = None
    def wire(self, wormhole, key, send):
        self._W = _interfaces.IWormhole(wormhole)
        self._K = _interfaces.IKey(key)
        self._S = _interfaces.ISend(send)

    @m.state(initial=True)
    def S0_unknown_key(self): pass
    @m.state()
    def S1_unverified_key(self): pass
    @m.state()
    def S2_verified_key(self): pass
    @m.state(terminal=True)
    def S3_scared(self): pass

    def got_message(self, phase, body):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        assert self._key
        data_key = derive_phase_key(self._side, phase)
        try:
            plaintext = decrypt_data(data_key, body)
        except CryptoError:
            self.got_message_bad()
            return
        self.got_message_good(phase, plaintext)

    @m.input()
    def got_key(self, key): pass
    @m.input()
    def got_message_good(self, phase, plaintext): pass
    @m.input()
    def got_message_bad(self): pass

    @m.output()
    def record_key(self, key):
        self._key = key
    @m.output()
    def S_got_verified_key(self, phase, plaintext):
        assert self._key
        self._S.got_verified_key(self._key)
    @m.output()
    def W_happy(self, phase, plaintext):
        self._W.happy()
    @m.output()
    def W_got_message(self, phase, plaintext):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(plaintext, type(b"")), type(plaintext)
        self._W.got_message(phase, plaintext)
    @m.output()
    def W_scared(self):
        self._W.scared()

    S0_unknown_key.upon(got_key, enter=S1_unverified_key, outputs=[record_key])
    S1_unverified_key.upon(got_message_good, enter=S2_verified_key,
                           outputs=[S_got_verified_key, W_happy, W_got_message])
    S1_unverified_key.upon(got_message_bad, enter=S3_scared,
                           outputs=[W_scared])
    S2_verified_key.upon(got_message_bad, enter=S3_scared,
                         outputs=[W_scared])
    S2_verified_key.upon(got_message_good, enter=S2_verified_key,
                         outputs=[W_got_message])
    S3_scared.upon(got_message_good, enter=S3_scared, outputs=[])
    S3_scared.upon(got_message_bad, enter=S3_scared, outputs=[])

