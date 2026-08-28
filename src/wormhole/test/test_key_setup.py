from spake2 import SPAKE2_Symmetric

import pytest

from .. import errors, timing
from .._encryption import derive_phase_key, decrypt_data, encrypt_data, negotiate
from .._key_setup.key_setup_v0 import KeySetup_V0
from .._key_setup.ikeysetup import Send, HaveAllegedKey, Done
from ..util import (bytes_to_hexstr, hexstr_to_bytes,
                    bytes_to_dict, dict_to_bytes,
                    to_bytes)

# Exercise key setup.

appid = "appid"
app_versions = { "foo": "bar" }
side1 = "side1"
side2 = "side2"
side3 = "side3"

# there are three events that involve a KeySetup instance:
# * A: start()
# * B: input("pake")
# * C: input("version")
# A is from our side, B and C are from our peer.

# The peer cannot correctly generate VERSION (C) until it receives
# data from our PAKE-0 message (A) (but a misbehaving peer might
# pretend to do that, so reject it).

#  * ABC - ok: textbook flow
#  * ACB - ok: message race on server
#  * BAC - ok: we waited for version resolution before starting
#  * BCA - causality violation, ScaredError
#  * CAB - causality violation, ScaredError
#  * CBA - causality violation, ScaredError

# We test all six.

# v0: the original SPAKE2-only protocol used by at least <=0.24.0

def test_v0_ABC():
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    pieces = ks.start(code, side2)
    assert "pake_v1" in pieces
    assert ks.output() == None

    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_key = derive_phase_key(key, side2, "version")
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    msg3 = encrypt_data(side2_version_key, side2_version_bytes)

    # B: feed it a PAKE, it should get an alleged key and transmit a VERSION
    ks.input(side2, "pake", msg2)
    assert ks.output() == HaveAllegedKey(key)
    v = ks.output()
    assert isinstance(v, Send)
    assert v.phase == "version"
    outbound_version_bytes = v.body
    assert ks.output() == None

    # verify outbound VERSION
    side1_version_key = derive_phase_key(key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

    # C: submit a VERSION, and it should verify it
    ks.input(side2, "version", msg3)
    assert ks.output() == Done(key, side2_version_bytes)
    assert ks.output() == None

def test_v0_ACB():
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    pieces = ks.start(code, side2)
    assert "pake_v1" in pieces
    assert ks.output() == None

    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_key = derive_phase_key(key, side2, "version")
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    msg3 = encrypt_data(side2_version_key, side2_version_bytes)

    # C: submit a VERSION, should be queued without actions
    ks.input(side2, "version", msg3)
    assert ks.output() == None

    # B: feed it a PAKE, it should get an alleged key and transmit a
    # VERSION, and then process the inbound VERSION to verify it
    ks.input(side2, "pake", msg2)
    assert ks.output() == HaveAllegedKey(key)
    v = ks.output()
    assert isinstance(v, Send)
    assert v.phase == "version"
    outbound_version_bytes = v.body
    assert ks.output() == Done(key, side2_version_bytes)
    assert ks.output() == None

    # verify outbound VERSION
    side1_version_key = derive_phase_key(key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

def test_v0_BAC():
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # B: feed it a PAKE, nothing should happen yet
    ks.input(side2, "pake", msg2)
    assert ks.output() == None

    # A: trigger the KeySetup to help us build the PAKE message,
    # this should get an alleged key, transmit VERSION, and be waiting
    # for the peer's VERSION
    pieces = ks.start(code, side2)
    assert "pake_v1" in pieces
    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))

    assert ks.output() == HaveAllegedKey(key)
    v = ks.output()
    assert isinstance(v, Send)
    assert v.phase == "version"
    outbound_version_bytes = v.body
    assert ks.output() == None

    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_key = derive_phase_key(key, side2, "version")
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    msg3 = encrypt_data(side2_version_key, side2_version_bytes)
    # verify outbound VERSION
    side1_version_key = derive_phase_key(key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

    # C: submit a VERSION, and it should verify it
    ks.input(side2, "version", msg3)
    assert ks.output() == Done(key, side2_version_bytes)
    assert ks.output() == None

# these three should cause errors
def test_v0_BCA():
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # B: feed it a PAKE, nothing should happen yet
    ks.input(side2, "pake", msg2)
    assert ks.output() == None

    # C: submit VERSION, bogus because they don't know our PAKE yet
    msg3 = b"you can't possibly have a session key yet"
    with pytest.raises(errors.CausalityError):
        ks.input(side2, "version", msg3)

def test_v0_C(): # CAB and CBA
    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # C: submit VERSION, bogus because they don't know our PAKE yet
    msg3 = b"you can't possibly have a session key yet"
    with pytest.raises(errors.CausalityError):
        ks.input(side2, "version", msg3)

# use a bad key for the VERSION message to trigger WrongPasswordError
def test_v0_bad_version(): # ABC
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    pieces = ks.start(code, side2)
    assert "pake_v1" in pieces
    assert ks.output() == None

    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    # build an inbound VERSION with the wrong key
    side2_app_versions = { "rah": "blurg" }
    side2_version_key = derive_phase_key(key, side2, "version WRONG")
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    msg3 = encrypt_data(side2_version_key, side2_version_bytes)

    # B: feed it a PAKE, it should get an alleged key and transmit a VERSION
    ks.input(side2, "pake", msg2)
    assert ks.output() == HaveAllegedKey(key)
    v = ks.output()
    assert isinstance(v, Send)
    assert v.phase == "version"
    assert ks.output() == None

    # C: submit a VERSION, and it should throw
    with pytest.raises(errors.WrongPasswordError):
        ks.input(side2, "version", msg3)

# use different sides to trigger CrowdedError
def test_v0_crowded(): # ABC
    code = "1-code"
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    pieces = ks.start(code, side2)
    assert "pake_v1" in pieces
    assert ks.output() == None

    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_key = derive_phase_key(key, side2, "version")
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    msg3 = encrypt_data(side2_version_key, side2_version_bytes)

    # B: feed it a PAKE, it should get an alleged key and transmit a VERSION
    ks.input(side2, "pake", msg2)
    assert ks.output() == HaveAllegedKey(key)
    v = ks.output()
    assert isinstance(v, Send)
    assert v.phase == "version"
    assert ks.output() == None

    # C: submit a VERSION from a third side
    with pytest.raises(errors.CrowdedError):
        ks.input(side3, "version", msg3)


def test_negotiate():
    assert side2 > side1
    # side2 is leader
    assert negotiate(side1, side2, ["0"], ["0"]) == "0"
    # plain numbers are fine too, and easier to type here, but are
    # used by EncryptionCore because of truthyness tests
    assert negotiate(side1, side2, [0], [0]) == 0
    assert negotiate(side1, side2, [0,1], [0,1]) == 0
    assert negotiate(side1, side2, [1,0], [0,1]) == 0 # leader wins
    assert negotiate(side1, side2, [0,1], [1,0]) == 1 # leader wins
    assert negotiate(side1, side2, [0,1,2], [2,3,4]) == 2
    assert negotiate(side1, side2, [2,1,0], [4,3,2]) == 2
    assert negotiate(side1, side2, [0], [4]) == None
