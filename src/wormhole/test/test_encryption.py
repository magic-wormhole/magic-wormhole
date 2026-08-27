import json, binascii
import pytest

from spake2 import SPAKE2_Symmetric

from .. import _encryption, timing, errors
from .._encryption import derive_key, derive_phase_key, encrypt_data, decrypt_data
from .._encryption import B_GotKey, B_Happy, B_Scared, B_GotVerifier, B_GotMessage, M_AddMessage
from .._interfaces import IBoss, IMailbox
from ..util import bytes_to_hexstr, dict_to_bytes, hexstr_to_bytes, to_bytes
from .common import Dummy

CODE = "1-code"

def build_encryption_core(side="side1"):
    c = _encryption._EncryptionCore("appid", {}, side, timing.DebugTiming())
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

# return a core in the "unverified key" state
def do_key_setup():
    c = build_encryption_core()
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None

    key, msg2 = compute_key(CODE, body)
    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey(key)
    body = assert_MAddMessage(c.output(), "version")
    assert decrypt_version(key, body) == {}
    assert c.output() == None
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


def test_happy_path():
    """
    Hook two negotiation cores together. We know they succeed if
    both output a Done symbol with matching shared secrets.
    """
    # property-based testing-wise, "the property" is "matching keys"
    a = build_encryption_core("side_a")
    b = build_encryption_core("side_b")

    # perform first part for side A
    a.got_code(CODE)  # want return-value to be msg1
    # ...but we have to do this dance

    def relay_add_message(core0, side0, core1):
        """
        Drains all messages from 'core0' and feeds any
        M_AddMessage into 'core1' with the 'side0' string as the side.

        returns any unprocessed messages
        """
        others = []
        while (msg := core0.output()) is not None:
            if isinstance(msg, M_AddMessage):
                core1.got_message(side0, msg.phase, msg.body)
            else:
                others.append(msg)
        return others

    messages0 = relay_add_message(a, "side_a", b)
    b.got_code(CODE)
    messages1 = relay_add_message(b, "side_b", a)

    # drain the rest of the messages from both cores, and find their
    # resulting keys.
    messages0.extend(relay_add_message(a, "side_a", b))
    messages1.extend(relay_add_message(b, "side_b", a))

    print(messages0)
    print(messages1)
    assert messages0 == messages1, "Keys do not match"


def test_good_key():
    c = build_encryption_core()
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None
    key, msg2 = compute_key(CODE, body)
    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey(key)
    body = assert_MAddMessage(c.output(), "version")
    assert decrypt_version(key, body) == {}
    assert c.output() == None

    verifier = compute_verifier(key)
    version2 = encrypt_version(key, {})
    c.got_message("side2", "version", version2)
    assert c.output() == B_Happy()
    assert c.output() == B_GotVerifier(verifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == None

# A receiver using input_code() will choose the nameplate first, then
# the rest of the code. Once the nameplate is selected, we'll claim it
# and open the mailbox, which will cause the senders PAKE to arrive
# before the code has been set. Encryption() is supposed to stash the
# PAKE message until the code is set (allowing the PAKE computation to
# finish). This test exercises that PAKE-then-code sequence.

def test_reversed():
    c = build_encryption_core()

    sp, msg2 = compute_pake0(CODE)
    c.got_message("side2", "pake", msg2)
    assert c.output() == None

    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    key2 = finish_pake(sp, body)
    assert c.output() == B_GotKey(key2)
    assert_MAddMessage(c.output(), "version")
    assert c.output() == None

# Badly formatted PAKE0 messages should raise exceptions which cause
# the RendezvousConnector ws_message() handler to kick the boss into
# the ERRORY state. From here we only need to check that these raise
# exceptions.

def test_bad_pake0_format():
    c = build_encryption_core()
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None
    pake_1_json = body.decode("utf-8")
    pake_1 = json.loads(pake_1_json)
    # ["pake_v1"] value is a 66-char hex-encoded SPAKE2 group element
    assert list(pake_1.keys()) == ["pake_v1"]
    good_spake2 = pake_1["pake_v1"]
    bad_pake_d = {"not_pake_v1": "stuff"}
    with pytest.raises(KeyError):
        c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    c.got_code(CODE)
    bad_pake_d = {"pake_v1": ["not scalar bytes"]}
    # trips the hexstr_to_bytes type assertion
    with pytest.raises(AssertionError):
        c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    c.got_code(CODE)
    bad_pake_d = {"pake_v1": "non-hex (odd)"}
    # odd number of chars means it isn't hex, trips binascii
    with pytest.raises(binascii.Error):
        c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

    c = build_encryption_core()
    c.got_code(CODE)
    # the SPAKE2 message starts with "S" (for Symmetric") followed by
    # 32 bytes of the group element. The point represented by 0 is not
    # in the right group, and will get us a ValueError during decoding
    bad_pake_d = {"pake_v1": good_spake2[0:2] + "00"}
    with pytest.raises(ValueError):
        c.got_message("side2", "pake", dict_to_bytes(bad_pake_d))

# Clients are supposed to ignore (and not attempt to decrypt)
# unrecognized non-numeric non-key-setup phases. They will log.err()
# about them.
def test_ignored_phase(observe_errors):
    c = build_encryption_core()

    # unrecognized phase before key is established
    c.got_message("side2", "ignored_phase", b"ignored_body")
    assert c.output() == None
    er = observe_errors.flush(errors._UnknownPhaseError)
    assert er[0].getErrorMessage() == "received unknown phase 'ignored_phase'"
    assert len(er) == 1

    # establish unverified key
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None
    key, msg2 = compute_key(CODE, body)
    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey(key)
    body = assert_MAddMessage(c.output(), "version")
    assert decrypt_version(key, body) == {}
    assert c.output() == None

    # unrecognized phase before verification
    c.got_message("side2", "ignored_phase2", b"ignored")
    assert c.output() == None
    er = observe_errors.flush(errors._UnknownPhaseError)
    assert er[0].getErrorMessage() == "received unknown phase 'ignored_phase2'"
    assert len(er) == 1

    # verify key
    vmsg = encrypt_version(key, {})
    c.got_message("side2", "version", vmsg)
    assert c.output() == B_Happy()
    assert isinstance(c.output(), B_GotVerifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == None

    # unrecognized phase after verification
    c.got_message("side2", "ignored_phase3", b"ignored")
    assert c.output() == None
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
    c.got_message("side2", "version", bad_vmsg)
    assert c.output() == B_Scared()
    assert c.output() == None

    # being scared is permanent and even good messages should not
    # cause a response
    good_msg = encrypt_message(key, "0", b"ignored")
    c.got_message("side2", "0", good_msg)
    assert c.output() == None

def test_scary_message():
    c, key = do_key_setup()
    verifier = compute_verifier(key)
    vmsg = encrypt_version(key, {})
    c.got_message("side2", "version", vmsg)
    assert c.output() == B_Happy()
    assert c.output() == B_GotVerifier(verifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == None

    bad_msg = encrypt_message(key, "0", b"ignored", broken=True)
    c.got_message("side2", "0", bad_msg)
    assert c.output() == B_Scared()
    assert c.output() == None

    good_msg = encrypt_message(key, "1", b"ignored")
    c.got_message("side2", "1", good_msg)
    assert c.output() == None

# A VERSION message that arrives before we've generated our outbound
# PAKE0 is a CausalityError, because our peer cannot correctly
# generate one without the session key, which is derived from the data
# in our PAKE0 message. (the real rule is that we haven't generated
# and delivered all the necessary messages for key generation, so
# multi-phase key-setup protocols could have a stricter rule and
# require more than just PAKE0, so TODO if our negotiated protocol
# depends on later messages, throw CausalityError in more cases)

def test_causality_violation():
    c = build_encryption_core()

    badver = encrypt_version(b"guessed", {})
    c.got_message("side2", "version", badver)
    assert c.output() == B_Scared()
    assert c.output() == None

# The application is allowed to w.send() data before establishing a
# key or even providing a code.

def test_early_send():
    c = build_encryption_core()

    c.send("0", b"early")
    assert c.output() == None

    # setting the code should not trigger sends
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None
    key, msg2 = compute_key(CODE, body)

    # computing the key, but not verifying it, should not trigger sends
    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey(key)
    assert_MAddMessage(c.output(), "version")
    assert c.output() == None

    # key verification (delivering the VERSION message) unblocks sends
    verifier = compute_verifier(key)
    version2 = encrypt_version(key, {})
    c.got_message("side2", "version", version2)
    assert c.output() == B_Happy()
    assert c.output() == B_GotVerifier(verifier)
    assert_BGotMessage(c.output(), "version")
    body = assert_MAddMessage(c.output(), "0")
    assert decrypt_message(key, "0", body) == b"early"
    assert c.output() == None

    # once the key is verified, sends go through immediately
    c.send("1", b"late")
    body = assert_MAddMessage(c.output(), "1")
    assert decrypt_message(key, "1", body) == b"late"
    assert c.output() == None

# of course establishing the key first should allow sends to work
def test_send():
    c, key = do_key_setup()

    verifier = compute_verifier(key)
    version2 = encrypt_version(key, {})
    c.got_message("side2", "version", version2)
    assert c.output() == B_Happy()
    assert c.output() == B_GotVerifier(verifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == None

    # once the key is verified, sends go through immediately
    c.send("0", b"late")
    body = assert_MAddMessage(c.output(), "0")
    assert decrypt_message(key, "0", body) == b"late"
    assert c.output() == None

# correctly-encrypted messages can be received
def test_receive():
    c, key = do_key_setup()

    verifier = compute_verifier(key)
    version2 = encrypt_version(key, {})
    c.got_message("side2", "version", version2)
    assert c.output() == B_Happy()
    assert c.output() == B_GotVerifier(verifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == None

    good1 = encrypt_message(key, "0", b"data1")
    c.got_message("side2", "0", good1)
    assert c.output() == B_GotMessage("0", b"data1")
    assert c.output() == None

    good2 = encrypt_message(key, "1", b"data2")
    c.got_message("side2", "1", good2)
    assert c.output() == B_GotMessage("1", b"data2")
    assert c.output() == None

# The peer should send PAKE0 before VERSION, but the server might
# deliver them the other way around. It could also deliver encrypted
# DILATE-n or application phases early. EncryptionCore is required to
# deliver VERSION (to Boss) first, and only then deliver any encrypted
# phases (and we'll require that those are delivered in arrival order)

# TODO: future (v2) protocols will introduce PAKE-1/2/3 phases, so
# future tests will have more combinations to exercise

def _order_helper():
    c = build_encryption_core()
    c.got_code(CODE)
    body = assert_MAddMessage(c.output(), "pake")
    assert c.output() == None
    key, msg2 = compute_key(CODE, body)
    ver_msg = encrypt_version(key, {})
    def add(phase):
        phase_s = str(phase)
        phase_b = phase_s.encode("ascii")
        good_msg = encrypt_message(key, phase_s, phase_b)
        c.got_message("side2", phase_s, good_msg)
    return c, key, msg2, ver_msg, add

def test_order_PAKE_VERSION():
    c, key, msg2, ver_msg, add = _order_helper()

    add(0)
    assert c.output() == None
    add(1)
    assert c.output() == None

    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey( key)
    assert_MAddMessage(c.output(), "version")
    assert c.output() == None

    add(2)
    assert c.output() == None
    add(3)
    assert c.output() == None

    c.got_message("side2", "version", ver_msg)
    assert c.output() == B_Happy()
    assert isinstance(c.output(), B_GotVerifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == B_GotMessage("0", b"0")
    assert c.output() == B_GotMessage("1", b"1")
    assert c.output() == B_GotMessage("2", b"2")
    assert c.output() == B_GotMessage("3", b"3")
    assert c.output() == None

def test_order_VERSION_PAKE():
    c, key, msg2, ver_msg, add = _order_helper()

    add(0)
    assert c.output() == None
    add(1)
    assert c.output() == None

    c.got_message("side2", "version", ver_msg)
    assert c.output() == None

    add(2)
    assert c.output() == None
    add(3)
    assert c.output() == None

    c.got_message("side2", "pake", msg2)
    assert c.output() == B_GotKey(key)
    assert_MAddMessage(c.output(), "version")
    assert c.output() == B_Happy()
    assert isinstance(c.output(), B_GotVerifier)
    assert_BGotMessage(c.output(), "version")
    assert c.output() == B_GotMessage("0", b"0")
    assert c.output() == B_GotMessage("1", b"1")
    assert c.output() == B_GotMessage("2", b"2")
    assert c.output() == B_GotMessage("3", b"3")
    assert c.output() == None


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
