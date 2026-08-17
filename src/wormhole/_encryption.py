from hashlib import sha256

from attr import attrib, attrs
from attr.validators import instance_of
from automat import MethodicalMachine
from nacl import utils
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from spake2 import SPAKE2_Symmetric
from zope.interface import implementer

from . import _interfaces
from .util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                   hexstr_to_bytes, to_bytes, HKDF, provides)

CryptoError
__all__ = ["derive_key", "derive_phase_key", "CryptoError", "Encryption"]


def derive_key(key, purpose, length=SecretBox.KEY_SIZE):
    if not isinstance(key, bytes):
        raise TypeError(type(key))
    if not isinstance(purpose, bytes):
        raise TypeError(type(purpose))
    if not isinstance(length, int):
        raise TypeError(type(length))
    return HKDF(key, length, CTXinfo=purpose)


def derive_phase_key(key, side, phase):
    assert isinstance(side, str), type(side)
    assert isinstance(phase, str), type(phase)
    side_bytes = side.encode("ascii")
    phase_bytes = phase.encode("ascii")
    purpose = (b"wormhole:phase:" + sha256(side_bytes).digest() +
               sha256(phase_bytes).digest())
    return derive_key(key, purpose)


def decrypt_data(key, encrypted):
    assert isinstance(key, bytes), type(key)
    assert isinstance(encrypted, bytes), type(encrypted)
    assert len(key) == SecretBox.KEY_SIZE, len(key)
    box = SecretBox(key)
    data = box.decrypt(encrypted)
    return data


def encrypt_data(key, plaintext):
    assert isinstance(key, bytes), type(key)
    assert isinstance(plaintext, bytes), type(plaintext)
    assert len(key) == SecretBox.KEY_SIZE, len(key)
    box = SecretBox(key)
    nonce = utils.random(SecretBox.NONCE_SIZE)
    return box.encrypt(plaintext, nonce)


@attrs
@implementer(_interfaces.IEncryption)
class Encryption:
    _appid = attrib(validator=instance_of(str))
    _versions = attrib(validator=instance_of(dict))
    _side = attrib(validator=instance_of(str))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    _have_code = None # or the code
    _have_pake = None # or the PAKE message
    _key = None # or the session key
    _key_is_verified = False # or True
    _scared = False # or True

    def wire(self, boss, mailbox, send):
        self._B = _interfaces.IBoss(boss)
        self._M = _interfaces.IMailbox(mailbox)
        self._S = _interfaces.ISend(send)

    def set_trace(self, _tracer):
        pass # unimplemented on non-Automat machines for now

    # input from Boss
    def got_code(self, code):
        assert not self._have_code
        self._have_code = code
        self._build_pake(code)
        if self._have_pake:
            self._process_pake()

    # input from Order
    def got_pake(self, body):
        assert isinstance(body, bytes), type(body)
        assert not self._have_pake
        self._have_pake = body
        if self._have_code:
            self._process_pake()

    def _be_scared(self):
        self._scared = True
        self._B.scared()

    def _process_pake(self):
        assert not self._key
        payload = bytes_to_dict(self._have_pake)
        if "pake_v1" in payload:
            key = self._compute_key(hexstr_to_bytes(payload["pake_v1"]))
            self._got_key(key)
        else:
            self._be_scared()

    def _build_pake(self, code):
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(
                to_bytes(code), idSymmetric=to_bytes(self._appid))
            msg1 = self._sp.start()
        body = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg1)})
        self._M.add_message("pake", body) # PAKE

    def _compute_key(self, msg2):
        assert isinstance(msg2, bytes)
        with self._timing.add("pake2", waiting="crypto"):
            key = self._sp.finish(msg2)
        return key

    # this is also called by tests
    def _got_key(self, key):
        self._key = key # not yet verified
        # TODO: make B.got_key() an eventual send, since it will fire the
        # user/application-layer get_unverified_key() Deferred, and if that
        # calls back into other wormhole APIs, bad things will happen
        self._B.got_key(key) # unverified
        phase = "version"
        data_key = derive_phase_key(key, self._side, phase)
        plaintext = dict_to_bytes(self._versions)
        encrypted = encrypt_data(data_key, plaintext)
        self._M.add_message(phase, encrypted) # VERSION

    # input from Order, these are encrypted messages
    def got_encrypted(self, side, phase, body):
        if self._scared:
            return # ignore message
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        assert self._key
        data_key = derive_phase_key(self._key, side, phase)
        try:
            plaintext = decrypt_data(data_key, body)
        except CryptoError:
            self._be_scared()
            return
        if not self._key_is_verified:
            self._key_is_verified = True
            self._S.got_verified_key(self._key)
            self._B.happy()
            self._B.got_verifier(derive_key(self._key, b"wormhole:verifier"))
        self._B.got_message(phase, plaintext)

@attrs
@implementer(_interfaces.ISend)
class Send:
    _side = attrib(validator=instance_of(str))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._queue = []

    def wire(self, mailbox):
        self._M = _interfaces.IMailbox(mailbox)

    @m.state(initial=True)
    def S0_no_key(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S1_verified_key(self):
        pass  # pragma: no cover

    # from Receive
    @m.input()
    def got_verified_key(self, key):
        pass

    # from Boss
    @m.input()
    def send(self, phase, plaintext):
        pass

    @m.output()
    def queue(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        self._queue.append((phase, plaintext))

    @m.output()
    def record_key(self, key):
        self._key = key

    @m.output()
    def drain(self, key):
        del key
        for (phase, plaintext) in self._queue:
            self._encrypt_and_send(phase, plaintext)
        self._queue[:] = []

    @m.output()
    def deliver(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        self._encrypt_and_send(phase, plaintext)

    def _encrypt_and_send(self, phase, plaintext):
        assert self._key
        data_key = derive_phase_key(self._key, self._side, phase)
        encrypted = encrypt_data(data_key, plaintext)
        self._M.add_message(phase, encrypted)

    S0_no_key.upon(send, enter=S0_no_key, outputs=[queue])
    S0_no_key.upon(
        got_verified_key, enter=S1_verified_key, outputs=[record_key, drain])
    S1_verified_key.upon(send, enter=S1_verified_key, outputs=[deliver])

@attrs
@implementer(_interfaces.IOrder)
class Order:
    _side = attrib(validator=instance_of(str))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._encryption = None
        self._queue = []

    def wire(self, encryption):
        self._E = _interfaces.IEncryption(encryption)

    @m.state(initial=True)
    def S0_no_pake(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S1_yes_pake(self):
        pass  # pragma: no cover

    def got_message(self, side, phase, body):
        # print("ORDER[%s].got_message(%s)" % (self._side, phase))
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        if phase == "pake":
            self.got_pake(side, phase, body)
        else:
            self.got_non_pake(side, phase, body)

    @m.input()
    def got_pake(self, side, phase, body):
        pass

    @m.input()
    def got_non_pake(self, side, phase, body):
        pass

    @m.output()
    def queue(self, side, phase, body):
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        self._queue.append((side, phase, body))

    @m.output()
    def notify_encryption(self, side, phase, body):
        self._E.got_pake(body)

    @m.output()
    def drain(self, side, phase, body):
        del phase
        del body
        for (side, phase, body) in self._queue:
            self._deliver(side, phase, body)
        self._queue[:] = []

    @m.output()
    def deliver(self, side, phase, body):
        self._deliver(side, phase, body)

    def _deliver(self, side, phase, body):
        self._E.got_encrypted(side, phase, body)

    S0_no_pake.upon(got_non_pake, enter=S0_no_pake, outputs=[queue])
    S0_no_pake.upon(got_pake, enter=S1_yes_pake, outputs=[notify_encryption, drain])
    S1_yes_pake.upon(got_non_pake, enter=S1_yes_pake, outputs=[deliver])
