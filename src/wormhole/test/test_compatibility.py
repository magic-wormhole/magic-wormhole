import json
from spake2 import SPAKE2_Symmetric
from .. import (_encryption, timing)
from ..util import (bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, to_bytes)
from .._encryption import B_GotKey, M_AddMessage

def build_encryption_core():
    c = _encryption._EncryptionCore("appid", {}, "side", timing.DebugTiming())
    return c

def assert_MAddMessage(ev, phase):
    assert isinstance(ev, M_AddMessage)
    assert ev.phase == phase
    return ev.body

# clients should ignore unrecognized properties in the phase="pake"
# message (known as PAKE-0)

def test_ignore_unrecognized_pake0_properties():
    c = build_encryption_core()

    code = "1-foo"
    c.got_code(code)
    assert_MAddMessage(c.output(), "pake")
    assert c.output() == None

    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    # extra properties should be ignored
    pake0 = {"pake_v1": bytes_to_hexstr(msg2_bytes), "ignore_me": "stuff"}
    c.got_message("side2", "pake", dict_to_bytes(pake0))
    # the v0 protocol should compute the right key despite any extra
    # properties
    assert isinstance(c.output(), B_GotKey)
    assert_MAddMessage(c.output(), "version")

# the new "PAKE Versioning" spec puts a version offer in the PAKE-0
# message, where it will be ignored by v0-only clients (including the
# python 0.24.0 client). Unrecognized versions (from the future)
# should be ignored too.
def test_ignore_future_versions():
    c = build_encryption_core()

    code = "1-foo"
    c.got_code(code)
    body = assert_MAddMessage(c.output(), "pake")
    msg1_json = body.decode("utf-8")
    msg1 = json.loads(msg1_json)
    msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    key2 = sp.finish(msg1_bytes)

    # extra properties should be ignored
    pake0 = {"pake_v1": bytes_to_hexstr(msg2_bytes),
             "versions": ["v99999-future", "v0"],
             }
    c.got_message("side2", "pake", dict_to_bytes(pake0))
    # the v0 protocol should compute the right key despite any extra
    # properties
    assert c.output() == B_GotKey(key2)
    assert_MAddMessage(c.output(), "version")
    assert c.output() == None

    # TODO(v1): assert the client concluded that we're speaking v0
    # (but key2 wouldn't match if it did anything else)
