from __future__ import absolute_import, print_function, unicode_literals

from attr import attrib, attrs
from attr.validators import instance_of, provides
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces
from ._key import CryptoError, decrypt_data, derive_key, derive_phase_key


@attrs
@implementer(_interfaces.IReceive)
class Receive(object):
    _side = attrib(validator=instance_of(type(u"")))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._key = None

    def wire(self, boss, send):
        self._B = _interfaces.IBoss(boss)
        self._S = _interfaces.ISend(send)

    @m.state(initial=True)
    def S0_unknown_key(self):
        pass  # pragma: no cover

    @m.state()
    def S1_unverified_key(self):
        pass  # pragma: no cover

    @m.state()
    def S2_verified_key(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S3_scared(self):
        pass  # pragma: no cover

    # from Ordering
    def got_message(self, side, phase, body):
        assert isinstance(side, type("")), type(phase)
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        assert self._key
        data_key = derive_phase_key(self._key, side, phase)
        try:
            plaintext = decrypt_data(data_key, body)
        except CryptoError:
            self.got_message_bad()
            return
        self.got_message_good(phase, plaintext)

    @m.input()
    def got_message_good(self, phase, plaintext):
        pass

    @m.input()
    def got_message_bad(self):
        pass

    # from Key
    @m.input()
    def got_key(self, key):
        pass

    @m.output()
    def record_key(self, key):
        self._key = key

    @m.output()
    def S_got_verified_key(self, phase, plaintext):
        assert self._key
        self._S.got_verified_key(self._key)

    @m.output()
    def W_happy(self, phase, plaintext):
        self._B.happy()

    @m.output()
    def W_got_verifier(self, phase, plaintext):
        self._B.got_verifier(derive_key(self._key, b"wormhole:verifier"))

    @m.output()
    def W_got_message(self, phase, plaintext):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(plaintext, type(b"")), type(plaintext)
        self._B.got_message(phase, plaintext)

    @m.output()
    def W_scared(self):
        self._B.scared()

    S0_unknown_key.upon(got_key, enter=S1_unverified_key, outputs=[record_key])
    S1_unverified_key.upon(
        got_message_good,
        enter=S2_verified_key,
        outputs=[S_got_verified_key, W_happy, W_got_verifier, W_got_message])
    S1_unverified_key.upon(
        got_message_bad, enter=S3_scared, outputs=[W_scared])
    S2_verified_key.upon(got_message_bad, enter=S3_scared, outputs=[W_scared])
    S2_verified_key.upon(
        got_message_good, enter=S2_verified_key, outputs=[W_got_message])
    S3_scared.upon(got_message_good, enter=S3_scared, outputs=[])
    S3_scared.upon(got_message_bad, enter=S3_scared, outputs=[])
