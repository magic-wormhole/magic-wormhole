import re

from attrs import define, field, frozen
from zope.interface import implementer
from twisted.python import log

from ._interfaces import IEncryption, ITiming, IBoss, IMailbox
from .util import (dict_to_bytes,
                   provides,
                   derive_key, derive_phase_key,
                   encrypt_data, decrypt_data, CryptoError)
from .errors import WrongPasswordError, CausalityError, _UnknownPhaseError
from ._key_setup.ikeysetup import IKeySetup, Send, HaveAllegedKey, Done
from ._key_setup.key_setup_v0 import KeySetup_V0

__all__ = ["Encryption", "_EncryptionCore"]
# phase classifiers

DILATE_RE = re.compile(r'^dilate-(\d+)$')
NUMERIC_RE = re.compile(r'^\d+$')

def is_key_setup(phase):
    return phase == "pake" or phase.startswith("pake-") or phase == "version"
def is_dilation(phase):
    return bool(DILATE_RE.search(phase))
def is_numeric(phase):
    return bool(NUMERIC_RE.search(phase))

@frozen
class B_GotKey:
    key: bytes
@frozen
class B_Happy:
    pass
@frozen
class B_Scared:
    pass
@frozen
class B_GotVerifier:
    verifier: bytes
@frozen
class B_GotMessage:
    phase: str
    body: bytes
@frozen
class M_AddMessage:
    phase: str
    body: bytes

CoreOutput = B_GotKey | B_Happy | B_Scared | B_GotVerifier | B_GotMessage | M_AddMessage


# This class is the sans-io core of the Encryption machine

@define(slots=False)
class _EncryptionCore:
    _appid: str
    _app_versions: dict
    _side: str
    _timing: ITiming = field(validator=provides(ITiming))

    _have_code = False
    _alleged_key = None # or unverified session key
    _key = None # or verified session key
    _scared = False # or True

    def __attrs_post_init__(self):
        self._queued_received_encrypted = []
        self._queued_sends = []
        self._outputs: list[CoreOutput] = []
        ks0 = KeySetup_V0(self._side, self._appid, self._app_versions, self._timing)
        self._key_setup = IKeySetup(ks0)

    def _add_output(self, ev):
        self._outputs.append(ev)

    def output(self):
        # TODO: should we do this but with self._scared?
        #if self._error:
        #    raise self._error
        if self._outputs:
            return self._outputs.pop(0)
        return None

    ### Key Setup

    def _process_key_setup(self):
        while True:
            match self._key_setup.output():
                case None:
                    return
                case Send(phase, body):
                    self._add_output(M_AddMessage(phase, body))
                case HaveAllegedKey(key):
                    # TODO: remove key from here, leave it for "done"
                    self._add_output(B_GotKey(key)) # unverified
                case Done(key, version_bytes):
                    self._key = key
                    self._add_output(B_Happy())
                    verifier = derive_key(self._key, b"wormhole:verifier")
                    self._add_output(B_GotVerifier(verifier))
                    self._add_output(B_GotMessage("version", version_bytes))
                    self._drain_queued_received_encrypted()
                    self._drain_queued_sends()
                case _:
                    raise ValueError("unknown KeySetupOutput")

    # input from Boss
    def got_code(self, code):
        # self._have_code enables delivery of inbound key-setup messages
        self._have_code = True
        pieces = self._key_setup.start(code)
        body = dict_to_bytes(pieces)
        self._add_output(M_AddMessage("pake", body)) # PAKE
        self._process_key_setup()

    def _be_scared(self):
        self._scared = True
        self._add_output(B_Scared())

    ### inbound messages

    # input from Mailbox
    def got_message(self, side, phase, body):
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        if self._scared:
            return
        if is_key_setup(phase):
            try:
                self._key_setup.input(side, phase, body) # can throw
            except (WrongPasswordError, CausalityError):
                self._be_scared()
                return # TODO: want B.scared, maybe don't want others
            # Could get CrowdedError but only if Mailbox misbehaved.
            # Note that all errors in received messages (ws_message)
            # will mark the Boss as ERRORY, which stops everything
            self._process_key_setup()
        elif is_dilation(phase) or is_numeric(phase):
            self._queued_received_encrypted.append((side, phase, body))
            if self._key:
                self._drain_queued_received_encrypted()
        else:
            # unknown non-numeric phase: spec says to ignore. log.err
            # will flunk unit tests but should be invisible to apps
            log.err(_UnknownPhaseError(f"received unknown phase '{phase}'"))

    def _drain_queued_received_encrypted(self):
        assert self._key
        while self._queued_received_encrypted:
            (side, phase, body) = self._queued_received_encrypted.pop(0)
            # these are encrypted non-key-setup messages (DILATE-n or
            # app-level numeric phases)
            if self._scared:
                return # ignore message
            data_key = derive_phase_key(self._key, side, phase)
            try:
                plaintext = decrypt_data(data_key, body)
            except CryptoError:
                self._be_scared()
                return
            self._add_output(B_GotMessage(phase, plaintext))

    ### outbound messages

    # input from Boss and Dilation
    def send(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        self._queued_sends.append((phase, plaintext))
        if self._key:
            self._drain_queued_sends()

    def _drain_queued_sends(self):
        assert self._key
        while self._queued_sends:
            (phase, plaintext) = self._queued_sends.pop(0)
            data_key = derive_phase_key(self._key, self._side, phase)
            encrypted = encrypt_data(data_key, plaintext)
            self._add_output(M_AddMessage(phase, encrypted))


# and this class is the IO-capable frontend

@implementer(IEncryption)
class Encryption:
    def __init__(self, appid, versions, side, timing):
        self._core = _EncryptionCore(appid, versions, side, timing)
        self._test_count_received_messages = 0

    def wire(self, boss, mailbox):
        self._B = IBoss(boss)
        self._M = IMailbox(mailbox)

    def set_trace(self, _tracer):
        pass # unimplemented on non-Automat machines for now

    def _process_events(self):
        # ideally these would be eventual-sends, but I don't want to
        # build that much runtime, so we just use self._events as a
        # queue to tolerate accidental reentrancy. One concern is that
        # B.got_key() will fire the user/application-layer
        # get_unverified_key() Deferred, and if that calls back into
        # other wormhole APIs, bad things will happen
        while True:
            match self._core.output():
                case None:
                    return
                case B_GotKey(key):
                    self._B.got_key(key) # unverified
                case B_Happy():
                    self._B.happy()
                case B_Scared():
                    self._B.scared()
                case B_GotVerifier(verifier):
                    self._B.got_verifier(verifier)
                case B_GotMessage(phase, body):
                    self._B.got_message(phase, body)
                case M_AddMessage(phase, body):
                    self._M.add_message(phase, body)
                case _:
                    raise ValueError("bad selector")

    # input from Boss
    def got_code(self, code):
        self._core.got_code(code)
        self._process_events()

    # input from Mailbox
    def got_message(self, side, phase, body):
        self._test_count_received_messages += 1
        self._core.got_message(side, phase, body)
        self._process_events()

    # input from Boss and Dilation
    def send(self, phase, plaintext):
        self._core.send(phase, plaintext)
        self._process_events()

    # shortcut methods for unit tests

    def test_count_received_messages(self):
        return self._test_count_received_messages
