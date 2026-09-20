from attrs import frozen, define, field
from zope.interface import implementer
from . import inegotiator, ikeysetup
from .key_setup_v0 import KeySetup_V0
from .._interfaces import ITiming
from ..util import dict_to_bytes, provides

# constructors are sampled at construction time, for unit tests
KEY_SETUP_CONSTRUCTORS = {
    "v0": KeySetup_V0,
}

class UnknownState(Exception):
    pass
class IllegalCall(Exception):
    pass

# states

@frozen
class Waiting:
    pass
@frozen
class Negotiating:
    key_setup: ikeysetup.IKeySetup
    wanted: str
    # next is key_setup.input(phase=wanted)
@frozen
class Done:
    key: bytes

State = Waiting | Negotiating | Done

@implementer(inegotiator.INegotiator)
@define(slots=False)
class Negotiator:
    _appid: str
    _app_versions: dict
    _side: str
    _timing: ITiming = field(validator=provides(ITiming))

    def __attrs_post_init__(self):
        # sample at startup so tests can modify, copy() probably overkill
        self._key_setup_constructors = KEY_SETUP_CONSTRUCTORS.copy()

        self._state: State = Waiting()
        self._their_side: str | None = None # set by got_versions
        self._queued_inbound: dict(str, bytes) = {} # awaiting being wanted
        self._wanted: str | None = None
        self._outputs: list[inegotiator.NegotiatorAction] = []

    def _build_negotiator(self):
        # for now we only do v0
        ks0 = self._key_setup_constructors["v0"]
        key_setup = ks0(self._side, self._appid, self._app_versions, self._timing)
        return key_setup

    def _start_one_version(self, key_setup, code):
        data = key_setup.start_pake0(code, self._their_side)
        body = dict_to_bytes(data)
        self._outputs.append(inegotiator.Send("pake", body))
        wanted = "pake"
        return Negotiating(key_setup, wanted)

    def _drain_inbound(self):
        assert isinstance(self._state, Negotiating)
        ks = self._state.key_setup
        while self._state.wanted in self._queued_inbound:
            assert isinstance(self._state, Negotiating) # Done should clear wanted
            phase = self._state.wanted
            body = self._queued_inbound.pop(phase)
            (actions, wanted) = ks.input(self._their_side, phase, body)
            assert isinstance(actions, list)
            self._process_actions(actions)
            self._state = Negotiating(ks, wanted)

    def _process_actions(self, actions):
        for action in actions:
            match action:
                case ikeysetup.Send(side, phase, body):
                    assert side == self._side
                    self._outputs.append(inegotiator.Send(phase, body))
                case ikeysetup.HaveAllegedKey():
                    self._outputs.append(inegotiator.HaveAllegedKey())
                case ikeysetup.Done(key, version_data):
                    self._outputs.append(inegotiator.Done(key, version_data))
                case _:
                    raise ValueError("unknown KeySetupAction") # TODO name it


    def got_code(self, code: str) -> None:
        match self._state:
            case Waiting():
                key_setup = self._build_negotiator()
                self._state = self._start_one_version(key_setup, code)
                self._drain_inbound()
            case Negotiating() | Done():
                raise IllegalCall
            case _:
                raise UnknownState
        return self._get_actions()

    def got_key_setup_message(self, side: str, phase: str, body: bytes) -> None:
        if not self._their_side:
            self._their_side = side
        assert side == self._their_side
        assert phase not in self._queued_inbound
        self._queued_inbound[phase] = body
        if isinstance(self._state, Negotiating):
            self._drain_inbound()
        return self._get_actions()

    def _get_actions(self) -> list[inegotiator.NegotiatorAction]:
        actions = self._outputs[:]
        self._outputs.clear()
        return actions
