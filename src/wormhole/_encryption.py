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

# This defines all the versions we are capable+willing to speak, in
# decreasing order of preference. This list will be sampled at
# construction time, so unit tests can mock.patch the list, to
# simulate older clients and ensure they can interoperate. Each
# version here must have an IKeySetup provider in the code below.

KEY_SETUP_VERSIONS = ["v0"]

def negotiate(my_side, their_side, my_versions, their_versions):
    assert my_side != their_side
    if my_side > their_side:
        # I am the leader
        leader_versions = my_versions
        follower_versions = set(their_versions)
    else:
        leader_versions = their_versions
        follower_versions = set(my_versions)
    for v in leader_versions:
        if v in follower_versions:
            return v
    return None

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
        self._queued_received_encrypted = []
        self._queued_sends = []
        # these are held until we have a self._key_setup
        self._queued_key_setup = []
        self._outputs: list[CoreOutput] = []
        self._key_setup_versions = KEY_SETUP_VERSIONS.copy() # copy() probably overkill
        self._negotiation_panel = {}
        self._negotiated_version = None
        self._key_setup = None
        self._their_side = None
        self._ready = False # set by begin(), enables _build_pake0
        self._sent_pake0 = False

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
        while self._key_setup:
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

    def _create_key_setup(self, version):
        # for now, all versions need a SPAKE2
        with self._timing.add("pake1", waiting="crypto"):
            sph = SPAKE2_Helper(self._appid)
        # create exactly one IKeySetup
        match version:
            case "v0":
                return KeySetup_V0(self._side, self._appid, self._app_versions, self._timing, sph)
            # case "v1":
            #     return KeySetup_V1(..)
            # add new versions here
            case _:
                raise ValueError("bad version %s" % version)

    def _build_pake0_panel(self):
        assert self._code # for ks.start()
        # for now, all versions need a SPAKE2. (v0 was always
        # SPAKE2, the planned v1 is also only SPAKE2, and the
        # planned v2 is SPAKE2+MLKEM)
        with self._timing.add("pake1", waiting="crypto"):
            sph = SPAKE2_Helper(self._appid)

        # walk all implemented versions, collect an IKeySetup for each
        panel = {}
        if "v0" in self._key_setup_versions:
            ks0 = KeySetup_V0(self._side, self._appid, self._app_versions, self._timing, sph)
            panel["v0"] = ks0
        # if "v1" in self._key_setup_versions:
        #     ks1 = KeySetup_V1(..)
        #     panel["v1"] = ks1
        # add new versions here, sharing the SPAKE2 if they use it

        pake0 = {}
        # merge pieces from all versions into the PAKE0 dict. Any
        # duplicates must match exactly (e.g. both v0 and v1 use
        # SPAKE2, they must share the SPAKE2 instance, so both get the
        # same SPAKE2 first message)
        for ver,ks in panel.items():
            pieces = ks.start(self._code, self._their_side) # side always None
            assert ks.output() == None # should be waiting for peer PAKE0
            for key,value in pieces.items():
                assert isinstance(value, str)
                if key in pake0:
                    assert value == pake0[key]
                else:
                    pake0[key] = value

        return panel, pake0

    def _send_pake0(self):
        assert self._code
        assert not self._sent_pake0
        if self._negotiated_version:
            # we have already received their PAKE0 (and side), and
            # have settled the question of what version we're using.
            pake0 = self._key_setup.start(self._code, self._their_side) # side always present
        else:
            # we have to start without knowing the version. Prepare
            # KeySetups for every version we're being optimistic about
            self._negotiation_panel, pake0 = self._build_pake0_panel()

        # in both cases, we add our version offer and send
        assert "versions" not in pake0
        pake0["versions"] = self._key_setup_versions
        body = dict_to_bytes(pake0)
        self._add_output(M_AddMessage("pake", body)) # PAKE
        self._sent_pake0 = True


    # input from Boss
    def got_code(self, code):
        # having self._key_setup enables delivery of inbound key-setup messages
        self._code = code
        # TODO: don't mark ourselves as "ready" just yet. We'll get
        # performance improvements from the v2 (post-quantum) protocol
        # by deferring "ready" until we've received the peer's PAKE-0,
        # if possible (e.g. when we're the second party). The task is:
        # add code to the network connection path to call begin()
        # after pending messages have probably arrived, or when we're
        # the first party (allocate-code) so we have to speak
        # first. Then remove this "self._ready = True".
        self._ready = True # TODO remove me, call begin() instead
        self._maybe_send_pake0()
        self._process_key_setup()

    def begin(self):
        # Call this when we shouldn't wait any longer for messages
        # from our peer. The ideal approach is to get an OPENED(count)
        # from the mailbox server and fire it after 'count' messages
        # have arrived. Since we don't have OPENED, another approach
        # is to call it one second after we send the OPEN command. Or,
        # if we're allocating, fire it immediately.

        # this may be called late, ignore it
        self._ready = True
        self._maybe_send_pake0()
        self._process_key_setup()

    def _maybe_send_pake0(self):
        if self._code and self._ready and not self._sent_pake0:
            self._send_pake0()

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
            self._queued_key_setup.append((side, phase, body))
            if phase == "pake":
                self._negotiate_version(side, phase, body)
                self._ready = True
                self._maybe_send_pake0()
            if self._key_setup:
                self._drain_queued_key_setup()
        elif is_dilation(phase) or is_numeric(phase):
            self._queued_received_encrypted.append((side, phase, body))
            if self._key:
                self._drain_queued_received_encrypted()
        else:
            # unknown non-numeric phase: spec says to ignore. log.err
            # will flunk unit tests but should be invisible to apps
            log.err(_UnknownPhaseError(f"received unknown phase '{phase}'"))

    def _negotiate_version(self, side, phase, body):
        assert not self._key_setup
        payload = bytes_to_dict(body)
        their_versions = payload.get("versions", ["v0"])
        version = negotiate(self._side, side, self._key_setup_versions, their_versions)
        self._negotiated_version = version
        if self._negotiation_panel and version in self._negotiation_panel:
            self._key_setup = self._negotiation_panel[version]
            self._negotiation_panel = None
        if not self._key_setup:
            # we may or may not have a code by this point
            self._key_setup = self._create_key_setup(version)

    def _drain_queued_key_setup(self):
        assert self._key_setup
        while self._queued_key_setup:
            (side, phase, body) = self._queued_key_setup.pop(0)
            try:
                self._key_setup.input(side, phase, body) # can throw
            except (WrongPasswordError, CausalityError):
                self._be_scared()
                return # TODO: want B.scared, maybe don't want others
            # Could get CrowdedError but only if Mailbox misbehaved.
            # Note that all errors in received messages (ws_message)
            # will mark the Boss as ERRORY, which stops everything
            self._process_key_setup()

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
