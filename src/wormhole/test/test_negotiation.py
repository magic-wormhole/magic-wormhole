from unittest import mock
from zope.interface import implementer

from .. import timing
from .._key_setup.negotiator import Negotiator
from .._key_setup import ikeysetup, inegotiator
from .._key_setup.key_setup_v0 import KeySetup_V0
from ..util import dict_to_bytes

code = "1-code"
side1 = "side1"
side2 = "side2"
appid = "appid"
app_versions = {}

@implementer(ikeysetup.IKeySetup)
class FakeKeySetup:
    def __init__(self):
        self._calls = []
        self._expected = []

    def t_expect(self, meth, result):
        self._expected.append((meth, result))
    def t_all_called(self):
        assert self._expected == []
        calls = self._calls
        self._calls = []
        return calls

    def _check(self, expected_method, *args):
        (meth, result) = self._expected.pop(0)
        assert meth == expected_method
        self._calls.append((expected_method, *args))
        return result

    def start_pake0(self, code, their_side):
        return self._check("start_pake0", code, their_side)

    def input(self, side, phase, body):
        return self._check("input", side, phase, body)

timing = timing.DebugTiming()

# For v0, which has only PAKE and VERSION, there are only orderings

# A=got_key_setup_message(pake), B=got_key_setup_message(version)

def test_v0_AB_basic():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c}):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # getting the code should trigger the Send(PAKE)
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.got_code(code)
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    # assume dict_to_bytes is deterministic
    exp_pake0 = {"pake_v1": "stuff"}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # receiving the inbound PAKE-0 body will build the alleged key,
    # emit VERSION, and wait for VERSION
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # A
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # and the peer's VERSION will verify the key, and stop waiting
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # B
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_BA():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c}):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # getting the code should trigger the Send(PAKE)
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.got_code(code)
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    # assume dict_to_bytes is deterministic
    exp_pake0 = {"pake_v1": "stuff"}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # receiving VERSION early will queue it until the PAKE arrives
    actions = n.got_key_setup_message(side2, "version", b"vct2") # B
    assert actions == []

    # receiving the inbound PAKE-0 body will build the alleged key,
    # emit VERSION, then process the queued VERSION
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # A
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b),
                                  ("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []
