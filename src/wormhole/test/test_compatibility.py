import json
from spake2 import SPAKE2_Symmetric
from .. import (_encryption, timing)
from ..util import (bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, to_bytes,
                    derive_phase_key, encrypt_data)
from .._encryption import B_HaveAllegedKey, B_Happy, B_GotAppVersions, M_AddMessage

def build_encryption_core():
    c = _encryption._EncryptionCore("appid", {}, "side", timing.DebugTiming())
    return c

def assert_MAddMessage(ev, phase):
    assert isinstance(ev, M_AddMessage)
    assert ev.phase == phase
    return ev.body

# encrypt into side1
def encrypt_version(key, app_versions):
    data_key = derive_phase_key(key, "side2", "version")
    plaintext = dict_to_bytes(app_versions)
    encrypted = encrypt_data(data_key, plaintext)
    return encrypted

# clients should ignore unrecognized properties in the phase="pake"
# message (known as PAKE-0)

def test_ignore_unrecognized_pake0_properties():
    c = build_encryption_core()

    code = "1-foo"
    actions = c.got_code(code)
    assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []

    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    # extra properties should be ignored
    pake0 = {"pake_v1": bytes_to_hexstr(msg2_bytes), "ignore_me": "stuff"}
    actions = c.got_message("side2", "pake", dict_to_bytes(pake0))
    # the v0 protocol should compute the right key despite any extra
    # properties
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions == []

# the new "PAKE Versioning" spec puts a version offer in the PAKE-0
# message, where it will be ignored by v0-only clients (including the
# python 0.24.0 client). Unrecognized versions (from the future)
# should be ignored too.
def test_ignore_future_versions():
    c = build_encryption_core()

    code = "1-foo"
    actions = c.got_code(code)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    msg1_json = body.decode("utf-8")
    msg1 = json.loads(msg1_json)
    msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    key2 = sp.finish(msg1_bytes)

    # extra properties should be ignored
    pake0 = {"pake_v1": bytes_to_hexstr(msg2_bytes),
             "my_key_setup_versions": ["v99999-future", "v0"],
             }
    actions = c.got_message("side2", "pake", dict_to_bytes(pake0))
    # the v0 protocol should compute the right key despite any extra
    # properties
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions == []

    vmsg2 = encrypt_version(key2, {})
    actions = c.got_message("side2", "version", vmsg2)
    assert actions.pop(0) == B_Happy(key2)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    assert actions == []

    # TODO(v1): assert the client concluded that we're speaking v0
    # (but key2 wouldn't match if it did anything else)
