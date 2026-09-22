import re
import pytest

from spake2 import SPAKE2_Symmetric
from cryptography.hazmat.primitives.asymmetric import mlkem

from .. import errors, timing
from ..util import derive_phase_key, decrypt_data, encrypt_data, HKDF
from .._key_setup.hash_transcript import hash_transcript
from .._key_setup.key_setup_v0 import KeySetup_V0
from .._key_setup.key_setup_v1 import KeySetup_V1
from .._key_setup.key_setup_v2 import KeySetup_V2
from .._key_setup.ikeysetup import (Send, HaveAllegedKey, Done)
from ..util import (bytes_to_hexstr, hexstr_to_bytes,
                    bytes_to_dict, dict_to_bytes,
                    to_bytes)

# Exercise key setup.

code = "1-code"
appid = "appid"
app_versions = { "foo": "bar" }
side1 = "side1"
side2 = "side2"
side3 = "side3"

def make_pake0(pieces, versions):
    pake0 = pieces.copy()
    pake0["my_key_setup_versions"] = versions
    return pake0

class Transcript:
    def __init__(self):
        self._transcript = []
    def add(self, side, phase, body):
        assert isinstance(side, str)
        assert isinstance(phase, str)
        assert isinstance(body, bytes)
        self._transcript.append((side, phase, body))
    def hash(self, version):
        return hash_transcript(version, self._transcript)

# For v0, we only ever use start_pake0 (a v0 peer cannot handle a
# PAKE-1, so there's no way to hit the start_pake1 case). We may or
# may not know the peer's "side" when we start, but that doesn't
# influence the v0 protocol.

# v0: the original SPAKE2-only protocol used by at least <=0.24.0

def _test_v0(side_known_early, version_is_good):
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    #t = Transcript()
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    side_early = side2 if side_known_early else None
    pieces = ks.start_pake0(code, side_early)
    assert "pake_v1" in pieces
    pake0 = make_pake0(pieces, ["v0"])
    # v0 does not use the transcript, sadly
    #t.add("pake", side1, dict_to_bytes(pake0))

    wanted = ks.submit_outbound_pake0(pake0)
    assert wanted == "pake"

    # extract its SPAKE2 public value, and complete the protocol
    key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    side2_good_version_key = derive_phase_key(key, side2, "version")
    good_inbound_version_bytes = encrypt_data(side2_good_version_key, side2_version_bytes)
    side2_bad_version_key = derive_phase_key(key, side2, "WRONG")
    bad_inbound_version_bytes = encrypt_data(side2_bad_version_key, side2_version_bytes)

    # B: feed it a PAKE, it should get an alleged key and transmit a VERSION
    #t.add_output(ks, side2, "pake", msg2)
    (actions, wanted) = ks.input(side2, "pake", msg2)
    assert actions.pop(0) == HaveAllegedKey()
    s = actions.pop(0)
    assert isinstance(s, Send)
    assert s.side == side1
    assert s.phase == "version"
    outbound_version_bytes = s.body
    assert actions == []
    assert wanted == "version"

    # verify outbound VERSION
    side1_version_key = derive_phase_key(key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

    # C: submit a VERSION, and it should verify it
    if version_is_good:
        (actions, wanted) = ks.input(side2, "version", good_inbound_version_bytes)
        assert actions.pop(0) == Done(key, side2_version_bytes)
        assert actions == []
        assert wanted == None
    else:
        with pytest.raises(errors.WrongPasswordError):
            ks.input(side2, "version", bad_inbound_version_bytes)
        # the error is sticky
        with pytest.raises(errors.WrongPasswordError):
            ks.input(side2, "version", good_inbound_version_bytes)

def test_v0_good():
    _test_v0(side_known_early=True, version_is_good=True)
    _test_v0(side_known_early=False, version_is_good=True)

def test_v0_wrong_password():
    _test_v0(side_known_early=True, version_is_good=False)
    _test_v0(side_known_early=False, version_is_good=False)

def test_v0_errors():
    ks = KeySetup_V0(side1, appid, app_versions, timing.DebugTiming())
    pake0 = (side1, "pake", b"body")
    with pytest.raises(ValueError, match="v0 cannot be started late"):
        ks.start_pake1(code, side2, pake0)
    with pytest.raises(AssertionError):
        ks.input(b"non-str side", "phase", b"body")
    with pytest.raises(AssertionError):
        ks.input("side", b"non-str phase", b"body")
    with pytest.raises(AssertionError):
        ks.input("side", "phase", "non-bytes body")
    with pytest.raises(ValueError, match=re.escape("input() before start")):
        # this gets far enough to register side2
        ks.input(side2, "phase", b"body")
    with pytest.raises(errors.CrowdedError):
        ks.input(side3, "phase", b"body")
        # CrowdedError is sticky

# v1: the new SPAKE2-only protocol that exercises negotiation

def _test_v1(side_known_early, version_is_good):
    # this models our peer
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})

    # this is us
    t = Transcript()
    ks = KeySetup_V1(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    side_early = side2 if side_known_early else None
    pieces = ks.start_pake0(code, side_early)
    assert "pake_v1" in pieces
    pake0 = make_pake0(pieces, ["v1"])
    pake0b = dict_to_bytes(pake0)
    pake0mt = (side1, "pake", pake0b)
    t.add(side1, "pake", pake0b)

    wanted = ks.submit_outbound_pake0(pake0mt)
    assert wanted == "pake"

    pre_version = dict_to_bytes({"our_key_setup_version": "v1"})
    t.add(side2, "pake", msg2)
    # B: feed it a PAKE, it should get an alleged key and transmit a pre-VERSION
    (actions, wanted) = ks.input(side2, "pake", msg2)
    assert actions.pop(0) == HaveAllegedKey()
    assert actions.pop(0) == Send(side1, "pake-1", pre_version)
    # the pre-version does not go into the transcript, nor does VERSION
    s = actions.pop(0)
    assert isinstance(s, Send)
    assert s.side == side1
    assert s.phase == "version"
    outbound_version_bytes = s.body
    assert actions == []
    assert wanted == "pake-1"

    # extract its SPAKE2 public value, and complete the protocol
    spake2_key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    t_hash = t.hash("v1")
    skm = spake2_key + t_hash
    tag = b"magic-wormhole key setup"
    key = HKDF(skm, 32, CTXinfo=tag)

    # verify outbound VERSION
    side1_version_key = derive_phase_key(key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    side2_good_version_key = derive_phase_key(key, side2, "version")
    good_inbound_version_bytes = encrypt_data(side2_good_version_key, side2_version_bytes)
    side2_bad_version_key = derive_phase_key(key, side2, "WRONG")
    bad_inbound_version_bytes = encrypt_data(side2_bad_version_key, side2_version_bytes)

    # C: submit the pre-version, should not explode
    (actions, wanted) = ks.input(side2, "pake-1", pre_version)
    assert actions == []
    assert wanted == "version"

    # d: submit the VERSION, and it should verify it
    if version_is_good:
        (actions, wanted) = ks.input(side2, "version", good_inbound_version_bytes)
        assert actions.pop(0) == Done(key, side2_version_bytes)
        assert actions == []
        assert wanted == None
    else:
        with pytest.raises(errors.WrongPasswordError):
            ks.input(side2, "version", bad_inbound_version_bytes)
        # the error is sticky
        with pytest.raises(errors.WrongPasswordError):
            ks.input(side2, "version", good_inbound_version_bytes)

def test_v1_good():
    _test_v1(side_known_early=True, version_is_good=True)
    _test_v1(side_known_early=False, version_is_good=True)

def test_v1_wrong_password():
    _test_v1(side_known_early=True, version_is_good=False)
    _test_v1(side_known_early=False, version_is_good=False)


# v2: SPAKE2+MLKEM hybrid

# first test will have DUT be Leader, v2-optimistic
def test_v2():
    side1 = "99Leader"
    side2 = "22Follower"
    assert side1 > side2
    # this is us, the Device Under Test
    t = Transcript()
    ks = KeySetup_V2(side1, appid, app_versions, timing.DebugTiming())

    # A: trigger the KeySetup to help us build the PAKE message
    pieces = ks.start_pake0(code, side2)
    print("PIECES", pieces)
    assert "pake_v1" in pieces
    assert "v2_mlkem_pubkey" in pieces
    pake0 = make_pake0(pieces, ["v2"])
    pake0b = dict_to_bytes(pake0)
    pake0mt = (side1, "pake", pake0b)
    t.add(side1, "pake", pake0b)

    wanted = ks.submit_outbound_pake0(pake0mt)
    assert wanted == "pake"

    # build the response
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    spake2_msg2_bytes = sp.start()
    pk_bytes = hexstr_to_bytes(pieces["v2_mlkem_pubkey"])
    pubkey = mlkem.MLKEM768PublicKey.from_public_bytes(pk_bytes)
    mlkem_key, ct = pubkey.encapsulate()
    # note: this is faster than the real protocol, which puts pake_v1
    # in PAKE-1 and v2_mlkem_ciphertext in PAKE-2
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(spake2_msg2_bytes),
                          "v2_mlkem_ciphertext": bytes_to_hexstr(ct)})
    t.add(side2, "pake", msg2)

    # compute the key for later
    spake2_key = sp.finish(hexstr_to_bytes(pieces["pake_v1"]))
    t_hash = t.hash("v2")
    ikm = spake2_key + mlkem_key
    kcm_tag = b"magic-wormhole key setup key-confirmation"
    kcm_key = HKDF(ikm, 32, salt=t_hash, CTXinfo=kcm_tag)
    main_tag = b"magic-wormhole key setup main key"
    main_key = HKDF(ikm, 32, salt=t_hash, CTXinfo=main_tag)
    pre_version = dict_to_bytes({"our_key_setup_version": "v2"})

    # submit PAKE-0, should get alleged key, pre-VERSION, VERSION
    (actions, wanted) = ks.input(side2, "pake", msg2)
    assert actions[0] == HaveAllegedKey()
    # verify outbound pre-VERSION
    assert actions[1] == Send(side1, "pake-1", pre_version)
    # the pre-version does not go into the transcript, nor does VERSION
    s = actions[2]
    assert isinstance(s, Send)
    assert s.side == side1
    assert s.phase == "version"
    outbound_version_bytes = s.body
    assert len(actions) == 3
    assert wanted == "pake-1"

    # verify outbound VERSION
    side1_version_key = derive_phase_key(kcm_key, side1, "version")
    side1_version_bytes = decrypt_data(side1_version_key, outbound_version_bytes)
    side1_version = bytes_to_dict(side1_version_bytes)
    assert side1_version == app_versions

    # submit inbound pre-VERSION, should not explode
    (actions, wanted) = ks.input(side2, "pake-1", pre_version)
    assert actions == []
    assert wanted == "version"

    # build an inbound VERSION
    side2_app_versions = { "rah": "blurg" }
    side2_version_bytes = dict_to_bytes(side2_app_versions)
    side2_good_version_key = derive_phase_key(kcm_key, side2, "version")
    good_inbound_version_bytes = encrypt_data(side2_good_version_key, side2_version_bytes)

    # submit the VERSION, and it should verify it
    (actions, wanted) = ks.input(side2, "version", good_inbound_version_bytes)
    assert actions == [Done(main_key, side2_version_bytes)]
    assert wanted == None
