from __future__ import absolute_import, print_function, unicode_literals

from hashlib import sha256

import six
from attr import attrib, attrs
from attr.validators import instance_of, provides
from automat import MethodicalMachine
from hkdf import Hkdf
from nacl import utils
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from spake2 import SPAKE2_Symmetric
from zope.interface import implementer

from . import _interfaces
from .util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                   hexstr_to_bytes, to_bytes)

CryptoError
__all__ = ["derive_key", "derive_phase_key", "CryptoError", "Key"]


def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)


def derive_key(key, purpose, length=SecretBox.KEY_SIZE):
    if not isinstance(key, type(b"")):
        raise TypeError(type(key))
    if not isinstance(purpose, type(b"")):
        raise TypeError(type(purpose))
    if not isinstance(length, six.integer_types):
        raise TypeError(type(length))
    return HKDF(key, length, CTXinfo=purpose)


def derive_phase_key(key, side, phase):
    assert isinstance(side, type("")), type(side)
    assert isinstance(phase, type("")), type(phase)
    side_bytes = side.encode("ascii")
    phase_bytes = phase.encode("ascii")
    purpose = (b"wormhole:phase:" + sha256(side_bytes).digest() +
               sha256(phase_bytes).digest())
    return derive_key(key, purpose)


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


# the Key we expose to callers (Boss, Ordering) is responsible for sorting
# the two messages (got_code and got_pake), then delivering them to
# _SortedKey in the right order.


@attrs
@implementer(_interfaces.IKey)
class Key(object):
    _appid = attrib(validator=instance_of(type(u"")))
    _versions = attrib(validator=instance_of(dict))
    _side = attrib(validator=instance_of(type(u"")))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._SK = _SortedKey(self._appid, self._versions, self._side,
                              self._timing)
        self._debug_pake_stashed = False  # for tests

    def wire(self, boss, mailbox, receive):
        self._SK.wire(boss, mailbox, receive)

    @m.state(initial=True)
    def S00(self):
        pass  # pragma: no cover

    @m.state()
    def S01(self):
        pass  # pragma: no cover

    @m.state()
    def S10(self):
        pass  # pragma: no cover

    @m.state()
    def S11(self):
        pass  # pragma: no cover

    @m.input()
    def got_code(self, code):
        pass

    @m.input()
    def got_pake(self, body):
        pass

    @m.output()
    def stash_pake(self, body):
        self._pake = body
        self._debug_pake_stashed = True

    @m.output()
    def deliver_code(self, code):
        self._SK.got_code(code)

    @m.output()
    def deliver_pake(self, body):
        self._SK.got_pake(body)

    @m.output()
    def deliver_code_and_stashed_pake(self, code):
        self._SK.got_code(code)
        self._SK.got_pake(self._pake)

    S00.upon(got_code, enter=S10, outputs=[deliver_code])
    S10.upon(got_pake, enter=S11, outputs=[deliver_pake])
    S00.upon(got_pake, enter=S01, outputs=[stash_pake])
    S01.upon(got_code, enter=S11, outputs=[deliver_code_and_stashed_pake])


@attrs
class _SortedKey(object):
    _appid = attrib(validator=instance_of(type(u"")))
    _versions = attrib(validator=instance_of(dict))
    _side = attrib(validator=instance_of(type(u"")))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def wire(self, boss, mailbox, receive):
        self._B = _interfaces.IBoss(boss)
        self._M = _interfaces.IMailbox(mailbox)
        self._R = _interfaces.IReceive(receive)

    @m.state(initial=True)
    def S0_know_nothing(self):
        pass  # pragma: no cover

    @m.state()
    def S1_know_code(self):
        pass  # pragma: no cover

    @m.state()
    def S2_know_key(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S3_scared(self):
        pass  # pragma: no cover

    # from Boss
    @m.input()
    def got_code(self, code):
        pass

    # from Ordering
    def got_pake(self, body):
        assert isinstance(body, type(b"")), type(body)
        payload = bytes_to_dict(body)
        if "pake_v1" in payload:
            self.got_pake_good(hexstr_to_bytes(payload["pake_v1"]))
        else:
            self.got_pake_bad()

    @m.input()
    def got_pake_good(self, msg2):
        pass

    @m.input()
    def got_pake_bad(self):
        pass

    @m.output()
    def build_pake(self, code):
        with self._timing.add("pake1", waiting="crypto"):
            self._sp = SPAKE2_Symmetric(
                to_bytes(code), idSymmetric=to_bytes(self._appid))
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
        # TODO: make B.got_key() an eventual send, since it will fire the
        # user/application-layer get_unverified_key() Deferred, and if that
        # calls back into other wormhole APIs, bad things will happen
        self._B.got_key(key)
        phase = "version"
        data_key = derive_phase_key(key, self._side, phase)
        plaintext = dict_to_bytes(self._versions)
        encrypted = encrypt_data(data_key, plaintext)
        self._M.add_message(phase, encrypted)
        # TODO: R.got_key() needs to be eventual-send too, as it can trigger
        # app-level got_verifier() and got_message() Deferreds.
        self._R.got_key(key)

    S0_know_nothing.upon(got_code, enter=S1_know_code, outputs=[build_pake])
    S1_know_code.upon(got_pake_good, enter=S2_know_key, outputs=[compute_key])
    S1_know_code.upon(got_pake_bad, enter=S3_scared, outputs=[scared])
