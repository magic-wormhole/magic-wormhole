import json, binascii
import pytest

from spake2 import SPAKE2_Symmetric

from .. import _encryption, timing, errors
from .._encryption import B_HaveAllegedKey, B_Happy, B_GotAppVersions, B_Scared, B_GotMessage, M_AddMessage
from .._interfaces import IBoss, IMailbox
from ..util import derive_key, derive_phase_key, encrypt_data, decrypt_data
from ..util import bytes_to_hexstr, dict_to_bytes, hexstr_to_bytes, to_bytes
from .common import Dummy

CODE = "1-code"

def build_encryption_core():
    c = _encryption._EncryptionCore("appid", {}, "side1", timing.DebugTiming())
    return c

def compute_pake0(code):
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
    return sp, msg2
def finish_pake(sp, pake0):
    msg1_json = pake0.decode("utf-8")
    msg1 = json.loads(msg1_json)
    msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
    key2 = sp.finish(msg1_bytes)
    return key2

def compute_key(code, msg1):
    sp, msg2 = compute_pake0(code)
    key = finish_pake(sp, msg1)

    return key, msg2

def assert_MAddMessage(ev, phase):
    assert isinstance(ev, M_AddMessage)
    assert ev.phase == phase
    return ev.body
def assert_BGotMessage(ev, phase):
    assert isinstance(ev, B_GotMessage)
    assert ev.phase == phase
    return ev.body

# return a core in the v0 "unverified key" state
def do_key_setup():
    c = build_encryption_core()
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []

    key, msg2 = compute_key(CODE, body)
    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    body = assert_MAddMessage(actions.pop(0), "version")
    assert decrypt_version(key, body) == {}
    assert actions == []
    return c, key

def compute_verifier(key):
    return derive_key(key, b"wormhole:verifier")

# encrypt into side1
def encrypt_version(key, app_versions, broken=False):
    data_key = derive_phase_key(key, "side2", "version")
    if broken:
        data_key = derive_phase_key(b"broken", "side2", "version")
    plaintext = dict_to_bytes(app_versions)
    encrypted = encrypt_data(data_key, plaintext)
    return encrypted

# decrypt from side1
def decrypt_version(key, encrypted):
    data_key = derive_phase_key(key, "side1", "version")
    return json.loads(decrypt_data(data_key, encrypted))

# encrypt into side1
def encrypt_message(key, phase, plaintext, broken=False):
    data_key = derive_phase_key(key, "side2", phase)
    if broken:
        data_key = derive_phase_key(b"broken", "side2", phase)
    return encrypt_data(data_key, plaintext)

# decrypt from side1
def decrypt_message(key, phase, encrypted):
    data_key = derive_phase_key(key, "side1", phase)
    return decrypt_data(data_key, encrypted)



def test_good_key():
    c = build_encryption_core()
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    key, msg2 = compute_key(CODE, body)
    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    body = assert_MAddMessage(actions.pop(0), "version")
    assert decrypt_version(key, body) == {}
    assert actions == []

    app_versions2 = {}
    vbytes2 = dict_to_bytes(app_versions2)
    version2 = encrypt_version(key, app_versions2)
    actions = c.got_message("side2", "version", version2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(vbytes2)
    assert actions == []

# A receiver using input_code() will choose the nameplate first, then
# the rest of the code. Once the nameplate is selected, we'll claim it
# and open the mailbox, which will cause the senders PAKE to arrive
# before the code has been set. Encryption() is supposed to stash the
# PAKE message until the code is set (allowing the PAKE computation to
# finish). This test exercises that PAKE-then-code sequence.

def test_reversed():
    c = build_encryption_core()

    sp, msg2 = compute_pake0(CODE)
    actions = c.got_message("side2", "pake", msg2)
    assert actions == []

    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    key2 = finish_pake(sp, body)
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions == []

    app_versions2 = {}
    vbytes2 = dict_to_bytes(app_versions2)
    version2 = encrypt_version(key2, app_versions2)
    actions = c.got_message("side2", "version", version2)
    assert actions.pop(0) == B_Happy(key2)
    assert actions.pop(0) == B_GotAppVersions(vbytes2)
    assert actions == []

# Badly formatted PAKE0 messages should raise exceptions which cause
# the RendezvousConnector ws_message() handler to kick the boss into
# the ERRORY state. From here we only need to check that these raise
# exceptions.

def test_v0_bad_pake0_format():
    c = build_encryption_core()
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    pake_1_json = body.decode("utf-8")
    pake_1 = json.loads(pake_1_json)
    # ["pake_v1"] value is a 66-char hex-encoded SPAKE2 group element
    assert list(pake_1.keys()) == ["pake_v1", "my_key_setup_versions"]
    good_spake2 = pake_1["pake_v1"]

    # by omitting my_key_setup_versions:, we force v0, which requires
    # "pake_v1" in the PAKE-0
    bad_pake_d = {"not_pake_v1": "stuff"}
    with pytest.raises(errors.NegotiationError):
        actions = c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    actions = c.got_code(CODE)
    bad_pake_d = {"pake_v1": ["not scalar bytes"]}
    # trips the hexstr_to_bytes type assertion
    with pytest.raises(AssertionError):
        actions = c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    actions = c.got_code(CODE)
    bad_pake_d = {"pake_v1": "non-hex (odd)"}
    # odd number of chars means it isn't hex, trips binascii
    with pytest.raises(binascii.Error):
        actions = c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    actions = c.got_code(CODE)
    # the SPAKE2 message starts with "S" (for Symmetric") followed by
    # 32 bytes of the group element. The point represented by 0 is not
    # in the right group, and will get us a ValueError during decoding
    bad_pake_d = {"pake_v1": good_spake2[0:2] + "00"}
    with pytest.raises(ValueError):
        actions = c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

# Clients are supposed to ignore (and not attempt to decrypt)
# unrecognized non-numeric non-key-setup phases. They will log.err()
# about them.
def test_ignored_phase(observe_errors):
    c = build_encryption_core()

    # unrecognized phase before key is established
    actions = c.got_message("side2", "ignored_phase", b"ignored_body")
    assert actions == []
    er = observe_errors.flush(errors._UnknownPhaseError)
    assert er[0].getErrorMessage() == "received unknown phase 'ignored_phase'"
    assert len(er) == 1

    # establish unverified key
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    key, msg2 = compute_key(CODE, body)
    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    body = assert_MAddMessage(actions.pop(0), "version")
    assert decrypt_version(key, body) == {}
    assert actions == []

    # unrecognized phase before verification
    actions = c.got_message("side2", "ignored_phase2", b"ignored")
    assert actions == []
    er = observe_errors.flush(errors._UnknownPhaseError)
    assert er[0].getErrorMessage() == "received unknown phase 'ignored_phase2'"
    assert len(er) == 1

    # verify key
    app_versions2 = {}
    vbytes2 = dict_to_bytes(app_versions2)
    vmsg2 = encrypt_version(key, app_versions2)
    actions = c.got_message("side2", "version", vmsg2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(vbytes2)
    assert actions == []

    # unrecognized phase after verification
    actions = c.got_message("side2", "ignored_phase3", b"ignored")
    assert actions == []
    er = observe_errors.flush(errors._UnknownPhaseError)
    assert er[0].getErrorMessage() == "received unknown phase 'ignored_phase3'"
    assert len(er) == 1

# Correctly-formatted PAKE0 messages that use the wrong password
# should be detected when the VERSION message arrives and fails
# decryption. This should kick the boss into SCARY mode and *not*
# raise an exception: the Boss will notify the server and shut down
# the protocol.

def test_scary_version():
    c, key = do_key_setup()

    bad_vmsg = encrypt_version(key, {}, broken=True)
    actions = c.got_message("side2", "version", bad_vmsg)
    assert actions.pop(0) == B_Scared()
    assert actions == []

    # being scared is permanent and even good messages should not
    # cause a response
    good_msg = encrypt_message(key, "0", b"ignored")
    actions = c.got_message("side2", "0", good_msg)
    assert actions == []

def test_scary_message():
    c, key = do_key_setup()
    app_versions2 = {}
    vbytes2 = dict_to_bytes(app_versions2)
    vmsg2 = encrypt_version(key, app_versions2)
    actions = c.got_message("side2", "version", vmsg2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(vbytes2)
    assert actions == []

    bad_msg = encrypt_message(key, "0", b"ignored", broken=True)
    actions = c.got_message("side2", "0", bad_msg)
    assert actions.pop(0) == B_Scared()
    assert actions == []

    good_msg = encrypt_message(key, "1", b"ignored")
    actions = c.got_message("side2", "1", good_msg)
    assert actions == []

# The application is allowed to w.send() data before establishing a
# key or even providing a code.

def test_early_send():
    c = build_encryption_core()

    actions = c.send("0", b"early")
    assert actions == []

    # setting the code should not trigger sends
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    key, msg2 = compute_key(CODE, body)

    # computing the key, but not verifying it, should not trigger sends
    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions == []

    # key verification (delivering the VERSION message) unblocks sends
    version2 = encrypt_version(key, {})
    actions = c.got_message("side2", "version", version2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    body = assert_MAddMessage(actions.pop(0), "0")
    assert decrypt_message(key, "0", body) == b"early"
    assert actions == []

    # once the key is verified, sends go through immediately
    actions = c.send("1", b"late")
    body = assert_MAddMessage(actions.pop(0), "1")
    assert decrypt_message(key, "1", body) == b"late"
    assert actions == []

# of course establishing the key first should allow sends to work
def test_send():
    c, key = do_key_setup()

    version2 = encrypt_version(key, {})
    actions = c.got_message("side2", "version", version2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    assert actions == []

    # once the key is verified, sends go through immediately
    actions = c.send("0", b"late")
    body = assert_MAddMessage(actions.pop(0), "0")
    assert decrypt_message(key, "0", body) == b"late"
    assert actions == []

# correctly-encrypted messages can be received
def test_receive():
    c, key = do_key_setup()

    version2 = encrypt_version(key, {})
    actions = c.got_message("side2", "version", version2)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    assert actions == []

    good1 = encrypt_message(key, "0", b"data1")
    actions = c.got_message("side2", "0", good1)
    assert actions.pop(0) == B_GotMessage("0", b"data1")
    assert actions == []

    good2 = encrypt_message(key, "1", b"data2")
    actions = c.got_message("side2", "1", good2)
    assert actions.pop(0) == B_GotMessage("1", b"data2")
    assert actions == []

# The peer should send PAKE0 before VERSION, but the server might
# deliver them the other way around. It could also deliver encrypted
# DILATE-n or application phases early. EncryptionCore is required to
# deliver VERSION (to Boss) first, and only then deliver any encrypted
# phases (and we'll require that those are delivered in arrival order)

# TODO: future (v2) protocols will introduce PAKE-1/2/3 phases, so
# future tests will have more combinations to exercise

def _order_helper():
    c = build_encryption_core()
    actions = c.got_code(CODE)
    body = assert_MAddMessage(actions.pop(0), "pake")
    assert actions == []
    key, msg2 = compute_key(CODE, body)
    ver_msg = encrypt_version(key, {})
    def add(phase):
        phase_s = str(phase)
        phase_b = phase_s.encode("ascii")
        good_msg = encrypt_message(key, phase_s, phase_b)
        actions = c.got_message("side2", phase_s, good_msg)
        return actions
    return c, key, msg2, ver_msg, add

def test_order_PAKE_VERSION():
    c, key, msg2, ver_msg, add = _order_helper()

    actions = add(0)
    assert actions == []
    actions = add(1)
    assert actions == []

    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions == []

    actions = add(2)
    assert actions == []
    actions = add(3)
    assert actions == []

    actions = c.got_message("side2", "version", ver_msg)
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    assert actions.pop(0) == B_GotMessage("0", b"0")
    assert actions.pop(0) == B_GotMessage("1", b"1")
    assert actions.pop(0) == B_GotMessage("2", b"2")
    assert actions.pop(0) == B_GotMessage("3", b"3")
    assert actions == []

def test_order_VERSION_PAKE():
    c, key, msg2, ver_msg, add = _order_helper()

    actions = add(0)
    assert actions == []
    actions = add(1)
    assert actions == []

    actions = c.got_message("side2", "version", ver_msg)
    assert actions == []

    actions = add(2)
    assert actions == []
    actions = add(3)
    assert actions == []

    actions = c.got_message("side2", "pake", msg2)
    assert actions.pop(0) == B_HaveAllegedKey()
    assert_MAddMessage(actions.pop(0), "version")
    assert actions.pop(0) == B_Happy(key)
    assert actions.pop(0) == B_GotAppVersions(dict_to_bytes({}))
    assert actions.pop(0) == B_GotMessage("0", b"0")
    assert actions.pop(0) == B_GotMessage("1", b"1")
    assert actions.pop(0) == B_GotMessage("2", b"2")
    assert actions.pop(0) == B_GotMessage("3", b"3")
    assert actions == []


# quick test of the Encryption wrapper

def test_wrapper_good():
    events = []
    b = Dummy("b", events, IBoss, "happy", "scared",
              "got_key", "got_verifier", "got_message")
    m = Dummy("m", events, IMailbox, "add_message")
    e = _encryption.Encryption("appid", {}, "side1", timing.DebugTiming())
    e.wire(b, m)

    CODE = "1-code"
    sp, msg2 = compute_pake0(CODE)
    e.got_code(CODE)
    assert events[0][:2] == ("m.add_message", "pake")
    key2 = finish_pake(sp, events[0][2])
    key2
    assert len(events) == 1
    events.clear()
