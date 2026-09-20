from unittest import mock
from zope.interface import implementer

from .. import timing
from .._key_setup.negotiator import negotiate, Negotiator
from .._key_setup import ikeysetup, inegotiator
from .._key_setup.key_setup_v0 import KeySetup_V0
from ..util import dict_to_bytes

code = "1-code"
side1 = "side1"
side2 = "side2"
appid = "appid"
app_versions = {}

def test_version_selection():
    assert side2 > side1
    # side2 is leader
    assert negotiate(side1, side2, ["0"], ["0"]) == "0"
    # plain numbers are fine too, and easier to type here, but are not
    # used by EncryptionCore because of truthyness tests
    assert negotiate(side1, side2, [0], [0]) == 0
    assert negotiate(side1, side2, [0,1], [0,1]) == 1
    assert negotiate(side1, side2, [1,0], [0,1]) == 1 # leader wins
    assert negotiate(side1, side2, [0,1], [1,0]) == 0 # leader wins
    assert negotiate(side1, side2, [0,1,2], [2,3,4]) == 2
    assert negotiate(side1, side2, [2,1,0], [4,3,2]) == 2
    assert negotiate(side1, side2, [2,3,1,0], [4,3,2]) == 2
    assert negotiate(side1, side2, [0], [4]) == None

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

    def submit_outbound_pake0(self, pake0):
        return self._check("submit_outbound_pake0", pake0)

    def start_pake1(self, code, their_side, pake0):
        return self._check("start_pake1", code, their_side, pake0)

    def input(self, side, phase, body):
        return self._check("input", side, phase, body)

timing = timing.DebugTiming()

# For v0, which has only PAKE-0 and no other PAKE-n phases, there are
# four events that might happen.

# A=got_code(), B=ready(), C=got_versions+got_key_setup_message(pake0), D=got_key_setup_message(version)

# The peer cannot send VERSION before they receive the PAKE-0 that we
# send after got_code+ready, imposes two constraints D > A and D >
# B. Also EncryptionCore will always call ready() immediately after C,
# if not sooner.


# ABCD: ok
# ABDC: ok
# ACBD: ok
# ACDB: noncausal, ready() called late
# ADBC: noncausal
# ADCB: noncausal
# BACD: ok
# BADC: ok
# BCAD: ok
# BCDA: noncausal
# BDAC: noncausal
# BDCA: noncausal
# CABD: ready() called late
# CADB: ready() called late
# CBAD: ok
# CBDA: noncausal
# CDAB: ready() called late
# CDBA: ready() called late
# DABC (and all other D***): noncausal

# so we have seven orders to test


def test_v0_ABCD_basic():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # getting the code shouldn't either, because we haven't declared ready()
    actions = n.got_code(code) # A
    assert ks0c.call_count == 0
    assert actions == []

    # ready() should trigger creation of the speculative panel,
    # calling start_pake0() but not submit_outbound_pake0() yet. The
    # Negotiator should send the PAKE-0
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.ready() # B
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    # assume dict_to_bytes is deterministic
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # receiving versions from their PAKE-0 selects a winner, which
    # submits the outbound pake0, but we were optimistic so it doesn't
    # need to emit any new actions
    fv0.t_expect("submit_outbound_pake0", "pake")
    actions = n.got_versions(side2, ["v0"]) # C1
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    assert fv0.t_all_called() == [("submit_outbound_pake0", p0)]
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    # but receiving the inbound PAKE-0 body will build the alleged key,
    # emit VERSION, and wait for VERSION
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # and the peer's VERSION will verify the key, and stop waiting
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_ABDC():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # getting the code shouldn't either, because we haven't declared ready()
    actions = n.got_code(code) # A
    assert ks0c.call_count == 0
    assert actions == []

    # ready() should trigger creation of the speculative panel,
    # calling start_pake0(), but not submit_outbound_pake0. The
    # Negotiator should send the PAKE-0
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.ready() # B
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # the peer's early VERSION is queued in Negotiator until a winner
    # is selected
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == []
    assert actions == [] # still waiting for PAKE-0

    # receiving versions from their PAKE-0 selects a winner, and
    # submits the previous outbound PAKE-0, but doesn't deliver
    # anything else yet: KeySetup wants "pake" and all Negotiator has
    # is "version"
    fv0.t_expect("submit_outbound_pake0", "pake")
    actions = n.got_versions(side2, ["v0"]) # C1
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    assert fv0.t_all_called() == [("submit_outbound_pake0", p0)]
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    #assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]

    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    # We should now be in Negotiating, so when the PAKE-0 body
    # arrives, it will be sent to key_setup right away. That will
    # build the alleged key and emits VERSION.
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(),
                            ikeysetup.Send(side1, "version", b"vbytes")],
                           "version"))
    # then it gets the previously queued inbound VERSION, which
    # finishes everything
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b),
                                  ("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_ACBD():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # getting the code shouldn't either, because we haven't declared ready()
    actions = n.got_code(code) # A
    assert ks0c.call_count == 0
    assert actions == []

    # receiving versions from their PAKE-0 (while knowing the code)
    # jumps directly to a winner (no speculation) and starts it (with
    # their_side)
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    fv0.t_expect("submit_outbound_pake0", "pake")
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    actions = n.got_versions(side2, ["v0"]) # C1
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, side2),
                                  ("submit_outbound_pake0", p0)]
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # and the PAKE-0 body will trigger an alleged key and emit VERSION
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # ready() is a nop since we already got the versions
    actions = n.ready() # B
    assert fv0.t_all_called() == []
    assert actions == []

    # and the peer's VERSION will verify the key
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_BACD():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # ready() can't start speculating without the code
    actions = n.ready() # B
    assert actions == []
    assert ks0c.call_count == 0

    # getting the code while ready starts speculation
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.got_code(code) # A
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # receiving versions from their PAKE-0 selects a winner, which
    # submits the outbound pake0, but we were optimistic so it doesn't
    # need to emit any new actions
    fv0.t_expect("submit_outbound_pake0", "pake")
    actions = n.got_versions(side2, ["v0"]) # C1
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    assert fv0.t_all_called() == [("submit_outbound_pake0", p0)]
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    # but receiving the inbound PAKE-0 body will build the alleged key,
    # emit VERSION, and wait for VERSION
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # and the peer's VERSION will verify the key, and stop waiting
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_BADC():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # ready() can't start speculating without the code
    actions = n.ready() # B
    assert actions == []
    assert ks0c.call_count == 0

    # getting the code while ready starts speculation
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    actions = n.got_code(code) # A
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == [("start_pake0", code, None)]
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions == []

    # the peer's early VERSION is queued until a winner is selected
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == []
    assert actions == [] # still waiting for PAKE-0

    # receiving versions from their PAKE-0 selects a winner, and
    # submits the previous outbound PAKE-0, but doesn't deliver
    # anything else yet: KeySetup wants "pake" and all Negotiator has
    # is "version"
    fv0.t_expect("submit_outbound_pake0", "pake")
    actions = n.got_versions(side2, ["v0"]) # C1
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    assert fv0.t_all_called() == [("submit_outbound_pake0", p0)]
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    # We should now be in Negotiating, so when the PAKE-0 body
    # arrives, it will be sent to key_setup right away. That will
    # build the alleged key and emits VERSION.
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(),
                            ikeysetup.Send(side1, "version", b"vbytes")],
                           "version"))
    # then it gets the previously queued inbound VERSION, which
    # finishes everything
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == [("input", side2, "pake", pake0b),
                                  ("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_BCAD():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # ready() can't start speculating without the code
    actions = n.ready() # B
    assert actions == []
    assert ks0c.call_count == 0

    # receiving versions from the peer lets us create the correct
    # key_setup (no speculation), but we can't start it without the
    # code
    actions = n.got_versions(side2, ["v0"]) # C1
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == []
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    # so their PAKE-0 is queued inside the negotiator
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == []
    assert actions == []

    # receiving the code finally starts that key_setup (and with the
    # peer's side), which sends the outbound PAKE-0, then delivers the
    # queued inbound PAKE-0 to get the alleged key and send VERSION
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    fv0.t_expect("submit_outbound_pake0", "pake")
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    actions = n.got_code(code) # A
    assert fv0.t_all_called() == [("start_pake0", code, side2),
                                  ("submit_outbound_pake0", p0),
                                  ("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # and the peer's VERSION will verify the key
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []

def test_v0_CBAD():
    fv0 = FakeKeySetup()
    ks0c = mock.create_autospec(KeySetup_V0)
    ks0c.return_value = fv0
    with (mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"]),
          mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_CONSTRUCTORS", {"v0": ks0c})):
        n = Negotiator(appid, app_versions, side1, timing)
    # merely creating the Negotiator shouldn't create a KeySetup yet
    assert ks0c.call_count == 0

    # receiving versions from the peer lets us create the correct
    # key_setup (no speculation), but we can't start it without the
    # code
    actions = n.got_versions(side2, ["v0"]) # C1
    assert ks0c.call_count == 1
    assert fv0.t_all_called() == []
    assert actions.pop(0) == inegotiator.DecidedKeySetupVersion("v0")
    assert actions == []

    # so their PAKE-0 is queued
    pake0b = dict_to_bytes({"pake_v1": "stuff2"})
    actions = n.got_key_setup_message(side2, "pake", pake0b) # C2
    assert fv0.t_all_called() == []
    assert actions == []

    # ready() is a nop since we already got the versions
    actions = n.ready() # B
    assert fv0.t_all_called() == []
    assert actions == []

    # receiving the code finally starts that key_setup (and with the
    # peer's side), which sends the outbound PAKE-0, then delivers the
    # queued inbound PAKE-0 to get the alleged key and send VERSION
    fv0.t_expect("start_pake0", {"pake_v1": "stuff"})
    fv0.t_expect("submit_outbound_pake0", "pake")
    fv0.t_expect("input", ([ikeysetup.HaveAllegedKey(), ikeysetup.Send(side1, "version", b"vbytes")], "version"))
    exp_pake0 = {"pake_v1": "stuff", "my_key_setup_versions": ["v0"]}
    p0 = (side1, "pake", dict_to_bytes(exp_pake0))
    actions = n.got_code(code) # A
    assert fv0.t_all_called() == [("start_pake0", code, side2),
                                  ("submit_outbound_pake0", p0),
                                  ("input", side2, "pake", pake0b)]
    assert actions.pop(0) == inegotiator.Send("pake", dict_to_bytes(exp_pake0))
    assert actions.pop(0) == inegotiator.HaveAllegedKey()
    assert actions.pop(0) == inegotiator.Send("version", b"vbytes")
    assert actions == []

    # and the peer's VERSION will verify the key
    fv0.t_expect("input", ([ikeysetup.Done(b"key", b"vbytes2")], None))
    actions = n.got_key_setup_message(side2, "version", b"vct2") # D
    assert fv0.t_all_called() == [("input", side2, "version", b"vct2")]
    assert actions.pop(0) == inegotiator.Done(b"key", b"vbytes2")
    assert actions == []
