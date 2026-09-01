from attrs import frozen, define, field
from zope.interface import implementer, provides
from . import inegotiator, ikeysetup
from .next_phase import next_phase
from .spake2_helper import SPAKE2_Helper
from .key_setup_v0 import KeySetup_V0
from .key_setup_v1 import KeySetup_V1
from .._interfaces import ITiming
from ..util import dict_to_bytes

# This defines all the versions we are capable+willing to speak, in
# decreasing order of preference. This list will be sampled at
# construction time, so unit tests can mock.patch the list, to
# simulate older clients and ensure they can interoperate. Each
# version here must have an IKeySetup provider in the code below.

KEY_SETUP_VERSIONS = ["v0", "v1"]

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

class UnknownState(Exception):
    pass
class IllegalCall(Exception):
    pass

# states

@frozen
class Waiting:
    pass
@frozen
class WaitingReady:
    pass
@frozen
class WaitingCode:
    code: str
@frozen
class WaitingVersion:
    key_setup: ikeysetup.IKeySetup
@frozen
class Speculating:
    code: str
    panel: dict[str]
@frozen
class Negotiating:
    key_setup: ikeysetup.IKeySetup
@frozen
class Done:
    key: bytes

State = Waiting | WaitingReady | WaitingCode | WaitingVersion | Speculating | Negotiating | Done

@implementer(inegotiator.INegotiator)
@define
class Negotiator:
    _appid: str
    _app_versions: dict
    _side: str
    _timing: ITiming = field(validator=provides(ITiming))

    def __attrs_post_init__(self):
        # sample at startup so tests can modify, copy() probably overkill
        self._key_setup_versions = KEY_SETUP_VERSIONS.copy()

        self._state: State = Waiting()
        self._queued_inbound: list[tuple] = [] # awating conclusion
        self._transcript: list[(str,str,bytes)] = []
        self._delivered_transcript = False
        self._next_outbound_phase = "pake" # PAKE-0
        self._outputs: list[inegotiator.NegotiatorAction] = []
        self._their_side: str | None = None # set by got_versions

    def _send_pakeN(self, data: dict, inTranscript: bool):
        phase = self._next_outbound_phase
        body = dict_to_bytes(data)
        self._outputs.append(inegotiator.Send(phase, body)) # PAKE-N
        if inTranscript:
            assert not self._delivered_transcript
            self._transcript.append((self._side, phase, body))
        self._next_outbound_phase = next_phase(phase)

    def got_code(self, code: str) -> None:
        match self._state:
            case Waiting():
                self._state = WaitingCode(code)
            case WaitingReady():
                panel, pake0 = self._build_panel(code)
                assert "versions" not in pake0
                pake0["versions"] = self._key_setup_versions
                self._send_pakeN(pake0, True)
                self._state = Speculating(code, panel)
            case WaitingVersion(key_setup):
                assert self._their_side
                pake0 = key_setup.start(code, self._their_side)
                assert "versions" not in pake0
                pake0["versions"] = self._key_setup_versions
                self._send_pakeN(pake0, True)
                self._state = Negotiating(key_setup)
                self._drain_key_setup(key_setup)
            case WaitingCode() | Speculating() | Negotiating() | Done():
                raise IllegalCall
            case _:
                raise UnknownState

    def ready(self) -> None:
        match self._state:
            case Waiting():
                self._state = WaitingReady()
            case WaitingCode(code):
                panel, pake0 = self._build_panel(code)
                assert "versions" not in pake0
                pake0["versions"] = self._key_setup_versions
                self._send_pakeN(pake0, True)
                self._state = Speculating(code, panel)
            case WaitingReady() | WaitingVersion() | Speculating() | Negotiating() | Done():
                pass
            case _:
                raise UnknownState

    def got_versions(self, their_side: str, their_versions: list[str]) -> None:
        self._their_side = their_side
        version = negotiate(self._side, their_side, self._key_setup_versions, their_versions)
        match self._state:
            case Waiting() | WaitingReady():
                # TODO: assert not self._queued_inbound ??
                key_setup = self._build_negotiator(version) # not started yet
                self._state = WaitingVersion(key_setup)
            case WaitingCode(code):
                key_setup = self._build_negotiator(version)
                pake0 = key_setup.start(code, their_side)
                assert "versions" not in pake0
                pake0["versions"] = self._key_setup_versions
                self._send_pakeN(pake0, True)
                self._state = Negotiating(key_setup)
                self._drain_key_setup(key_setup)
            case Speculating(code, panel):
                if version in panel: # lucky
                    key_setup = panel[version]
                else: # unlucky
                    key_setup = self._build_negotiator(version)
                    pake1 = key_setup.start(code, their_side)
                    self._send_pakeN(pake1, True)
                self._state = Negotiating(key_setup)
                self._drain_key_setup(key_setup)
            case WaitingVersion() | Negotiating() | Done():
                raise IllegalCall
            case _:
                raise UnknownState

    def got_key_setup_message(self, side: str, phase: str, body: bytes) -> None:
        self._queued_inbound.append((side, phase, body))
        match self._state:
            case Negotiating():
                self._drain_inbound()

    def _drain_key_setup(self, key_setup):
        while True:
            match key_setup.output():
                case None:
                    break
                case ikeysetup.Send(body, inTranscript):
                    self._send_pakeN(body, inTranscript)
                case ikeysetup.AddToTranscript(side, phase, body):
                    assert not self._delivered_transcript
                    self._transcript.append((side, phase, body))
                case ikeysetup.GetTranscript():
                    assert not self._delivered_transcript
                    self._delivered_transcript = True
                    key_setup.transcriptIs(self._transcript)
                case ikeysetup.HaveAllegedKey(key):
                    self._outputs.append(inegotiator.HaveAllegedKey(key))
                case ikeysetup.SendVersion(encrypted):
                    self._outputs.append(inegotiator.Send("version", encrypted))
                case ikeysetup.Done(key, version_data):
                    self._outputs.append(inegotiator.Done(key, version_data))
                case _:
                    raise ValueError("unknown KeySetupAction") # TODO name it

    def _drain_inbound(self):
        assert isinstance(self._state, Negotiating)
        key_setup = Negotiating.key_setup
        while self._queued_inbound:
            (side, phase, body) = self._queued_inbound.pop(0)
            key_setup.got_message(side, phase, body)
            self._drain_key_setup(key_setup)

    def output(self) -> inegotiator.NegotiatorAction | None:
        if self._outputs:
            return self._outputs.pop(0)
        return None



    def _build_panel(self, code):
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
        if "v1" in self._key_setup_versions:
            ks1 = KeySetup_V1(self._side, self._appid, self._app_versions, self._timing, sph)
            panel["v1"] = ks1
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
            pieces = ks.start(self._code, None) # we don't know their_side yet
            assert ks.output() == None # should be waiting for peer PAKE0
            for key,value in pieces.items():
                assert isinstance(value, str)
                if key in pake0:
                    assert value == pake0[key]
                else:
                    pake0[key] = value

        return panel, pake0

    def _build_negotiator(self, version):
        # for now, all versions need a SPAKE2
        with self._timing.add("pake1", waiting="crypto"):
            sph = SPAKE2_Helper(self._appid)
        # create exactly one IKeySetup
        match version:
            case "v0":
                return KeySetup_V0(self._side, self._appid, self._app_versions, self._timing, sph)
            case "v1":
                return KeySetup_V1(self._side, self._appid, self._app_versions, self._timing, sph)
            # add new versions here
            # case "v1":
            #     return KeySetup_V1(..)
            case _:
                raise ValueError("bad version %s" % version)
