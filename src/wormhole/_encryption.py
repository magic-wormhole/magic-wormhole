import re

from attrs import define, field, frozen
from zope.interface import implementer
from twisted.python import log

from ._interfaces import IEncryption, ITiming, IBoss, IMailbox
from .util import (dict_to_bytes, bytes_to_dict, provides,
                   derive_key, derive_phase_key,
                   encrypt_data, decrypt_data, CryptoError)
from .errors import WrongPasswordError, CausalityError, _UnknownPhaseError
from ._key_setup.ikeysetup import Send, HaveAllegedKey, Done
from ._key_setup.key_setup_v0 import KeySetup_V0
from ._key_setup.spake2_helper import SPAKE2_Helper

__all__ = ["Encryption", "_EncryptionCore"]
# phase classifiers

DILATE_RE = re.compile(r'^dilate-(\d+)$')
NUMERIC_RE = re.compile(r'^\d+$')

# Every wormhole has an _EncryptionCore, wrapped by an IO-capable
# (well, invoking-other-machines -capable) "Encryption" object. The
# core has basically two modes: "negotiation" (where we're trying to
# establish a verified key) and "running" (where we're encrypting and
# decrypting regular phases). It holds a Negotiator for the
# negotiation phase, and handles the running mode itself.
#
# The Negotiator performs key-setup-version negotiation, and manages
# the key-setup process. There are multiple versions of the key-setup
# protocol, and each one is implemented by an IKeySetup
# instance. Depending upon how we're configured and what we learn
# about the peer's capabilities (and when we learn that), there may be
# multiple IKeySetup instances working in parallel (each for a
# different potential version), but at some point all are thrown away
# except for a single winner. That winning IKeySetup then continues
# working until the key is established and verified. Once that is
# complete, both the Negotiator and the IKeySetup are discarded.



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

# EncryptionCore has three key-setup input events: got_code(),
# begin(), got_message(pake0). Both begin() and got-pake0 make us
# "ready". When we have the code AND are ready, we create and send our
# own pake0. When we receive pake0 we can resolve the version. If we
# create-pake0 before resolving the version, we must make all
# optimistic IKeySetups and merge their pieces. If we create-pake0
# after resolving the version, we create only a single IKeySetup. If
# we resolve the version after creating multiple IKeySetups, and the
# resolved version was one of them, promote it, else create a new
# IKeySetup of the new version, in either case discard the rest.

# 1: CODE-BEGIN-sendpake0multi-PAKE0-resolve-deliver
# 2: BEGIN-CODE-sendpake0multi-PAKE0-resolve-deliver
# 3: CODE-PAKE0-resolve-sendpake0single-deliver-(BEGIN-nop)
# 4: BEGIN-PAKE0-resolve-deliver-CODE-sendpake0single
# 5: PAKE0-resolve-deliver-CODE-sendpake0single-(BEGIN-nop)
# 6: PAKE0-resolve-deliver-BEGIN-nop-CODE-sendpake0single

# This class is the sans-io core of the Encryption machine

@define(slots=False)
class _EncryptionCore:
    _appid: str
    _app_versions: dict
    _side: str
    _timing: ITiming = field(validator=provides(ITiming))

    _code = None
    _alleged_key = None # or unverified session key
    _key = None # or verified session key
    _scared = False # or True

    def __attrs_post_init__(self):
        self._outputs: list[CoreOutput] = []
        # these are held until we have a verified key
        self._queued_received_encrypted = []
        self._queued_sends = []

        self._negotiator = Negotiator(self._appid, self._app_versions, self._side, self._timing)
        self._their_side = None

    def _add_output(self, ev):
        self._outputs.append(ev)

    def output(self):
        # TODO: should we do this but with self._scared?
        #if self._error:
        #    raise self._error
        if self._outputs:
            return self._outputs.pop(0)
        return None


    # input from Boss
    def got_code(self, code):
        self._negotiator.got_code(code)
        # TODO: don't mark ourselves as "ready" just yet. We'll get
        # performance improvements from the v2 (post-quantum) protocol
        # by deferring "ready" until we've received the peer's PAKE-0,
        # if possible (e.g. when we're the second party). The task is:
        # add code to the network connection path to call begin()
        # after pending messages have probably arrived, or when we're
        # the first party (allocate-code) so we have to speak
        # first. Then remove this call to ready()
        self._negotiator.ready()
        self._process_negotiator()

    def begin(self):
        # Call this when we shouldn't wait any longer for messages
        # from our peer. The ideal approach is to get an OPENED(count)
        # from the mailbox server and fire it after 'count' messages
        # have arrived. Since we don't have OPENED, another approach
        # is to call it one second after we send the OPEN command. Or,
        # if we're allocating, fire it immediately.

        # this may be called late, ignore it
        self._negotiator.ready()
        self._process_negotiator()

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
        if not self._their_side:
            self._their_side = side
        assert side == self._their_side # Mailbox should catch this
        if is_key_setup(phase):
            if phase == "pake":
                data = bytes_to_dict(body)
                their_versions = data.get("my_key_setup_versions", ["v0"])
                self._negotiator.got_versions(side, their_versions)
            self._negotiator.got_key_setup_message(side, phase, body)
            self._process_negotiator()
        elif is_dilation(phase) or is_numeric(phase):
            self._queued_received_encrypted.append((side, phase, body))
            if self._key:
                self._drain_queued_received_encrypted()
        else:
            # unknown non-numeric phase: spec says to ignore. log.err
            # will flunk unit tests but should be invisible to apps
            log.err(_UnknownPhaseError(f"received unknown phase '{phase}'"))

    def _process_negotiator(self):
        match self._negotiator.output():
            case None:
                break
            case inegotiator.Send(phase, body):
                self._add_output(M_AddMessage(phase, body))
            case inegotiator.HaveAllegedKey(key):
                NotImplementedError XXX
            case inegotiator.Done(key, version_data):
                self._add_output(B_Happy())
                self._add_output(B_GotKey(key))
                "deliver version_data" XXX

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
