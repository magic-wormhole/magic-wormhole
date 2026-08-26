from attr import attrib, attrs
from attr.validators import instance_of
from spake2 import SPAKE2_Symmetric
from zope.interface import implementer

from . import _interfaces
from .util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                   hexstr_to_bytes, to_bytes, provides, CryptoError,
                   derive_key, derive_phase_key, decrypt_data, encrypt_data)

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

    def __attrs_post_init__(self):
        self._queued_received_encrypted = []
        self._queued_sends = []

    def wire(self, boss, mailbox):
        self._B = _interfaces.IBoss(boss)
        self._M = _interfaces.IMailbox(mailbox)

    def set_trace(self, _tracer):
        pass # unimplemented on non-Automat machines for now

    ### Key Setup

    # input from Boss
    def got_code(self, code):
        assert not self._have_code
        self._have_code = code
        self._build_pake(code)
        if self._have_pake:
            self._process_pake()

    def _build_pake(self, code):
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(
                to_bytes(code), idSymmetric=to_bytes(self._appid))
            msg1 = self._sp.start()
        body = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg1)})
        self._M.add_message("pake", body) # PAKE

    def _got_pake(self, body):
        assert isinstance(body, bytes), type(body)
        assert not self._have_pake
        self._have_pake = body
        if self._have_code:
            self._process_pake()

    def _process_pake(self):
        assert not self._key
        payload = bytes_to_dict(self._have_pake)
        if "pake_v1" in payload:
            key = self._compute_key(hexstr_to_bytes(payload["pake_v1"]))
            self._got_key(key)
        else:
            self._be_scared()

    def _compute_key(self, msg2):
        assert isinstance(msg2, bytes)
        with self._timing.add("pake2", waiting="crypto"):
            key = self._sp.finish(msg2)
        return key

    def _got_key(self, key):
        # this is also called by tests
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
        self._drain_queued_received_encrypted()

    def _got_verified_key(self):
        # this is also called by tests
        assert self._key_is_verified
        self._drain_queued_sends()
        self._B.happy()
        self._B.got_verifier(derive_key(self._key, b"wormhole:verifier"))

    def _be_scared(self):
        self._scared = True
        self._B.scared()

    ### inbound messages

    # input from Mailbox
    def got_message(self, side, phase, body):
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        if phase == "pake":
            self._got_pake(body) # TODO(v2): include side, protocol needs it
        else:
            if self._key:
                self._decrypt_and_deliver(side, phase, body)
            else:
                self._queued_received_encrypted.append((side, phase, body))

    def _decrypt_and_deliver(self, side, phase, body):
        # these are encrypted messages (VERSION, DILATE-n, or
        # app-level numeric phases)
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
            self._got_verified_key()
        self._B.got_message(phase, plaintext)

    def _drain_queued_received_encrypted(self):
        for (side, phase, body) in self._queued_received_encrypted:
            self._decrypt_and_deliver(side, phase, body)
        self._queued_received_encrypted.clear()

    ### outbound messages

    # input from Boss and Dilation
    def send(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        if self._key_is_verified:
            self._encrypt_and_send(phase, plaintext)
        else:
            self._queued_sends.append((phase, plaintext))

    def _encrypt_and_send(self, phase, plaintext):
        assert self._key
        assert self._key_is_verified
        data_key = derive_phase_key(self._key, self._side, phase)
        encrypted = encrypt_data(data_key, plaintext)
        self._M.add_message(phase, encrypted)

    def _drain_queued_sends(self):
        assert self._key
        assert self._key_is_verified
        for (phase, plaintext) in self._queued_sends:
            self._encrypt_and_send(phase, plaintext)
        self._queued_sends.clear()
