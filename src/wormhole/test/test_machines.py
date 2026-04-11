import json

from nacl.secret import SecretBox
from spake2 import SPAKE2_Symmetric
from zope.interface import directlyProvides, implementer

from unittest import mock

from .. import (__version__, _allocator, _boss, _code, _input, _key, _lister,
                _mailbox, _nameplate, _order, _receive, _rendezvous, _send,
                _terminator, errors, timing)
from .._interfaces import (IAllocator, IBoss, ICode, IDilator, IInput, IKey,
                           ILister, IMailbox, INameplate, IOrder, IReceive,
                           IRendezvousConnector, ISend, ITerminator, IWordlist,
                           ITorManager)
from .._key import derive_key, derive_phase_key, encrypt_data
from ..journal import ImmediateJournal
from .._status import WormholeStatus
from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, to_bytes)
import pytest
import pytest_twisted


@implementer(IWordlist)
class FakeWordList:
    def choose_words(self, length):
        return "-".join(["word"] * length)

    def get_completions(self, prefix):
        self._get_completions_prefix = prefix
        return self._completions


class Dummy:
    def __init__(self, name, events, iface, *meths, **kw):
        self.name = name
        self.events = events
        if iface:
            directlyProvides(self, iface)
        for meth in meths:
            self.mock(meth)
        self.retval = None
        for k, v in kw.items():
            setattr(self, k, v)

    def mock(self, meth):
        def log(*args):
            self.events.append((f"{self.name}.{meth}", ) + args)
            return self.retval

        setattr(self, meth, log)


def build_send():
    events = []
    s = _send.Send("side", timing.DebugTiming())
    m = Dummy("m", events, IMailbox, "add_message")
    s.wire(m)
    return s, m, events


def test_send_first():
    s, m, events = build_send()
    s.send("phase1", b"msg")
    assert events == []
    key = b"\x00" * 32
    nonce1 = b"\x00" * SecretBox.NONCE_SIZE
    with mock.patch("nacl.utils.random", side_effect=[nonce1]) as r:
        s.got_verified_key(key)
    assert r.mock_calls == [mock.call(SecretBox.NONCE_SIZE)]
    # print(bytes_to_hexstr(events[0][2]))
    enc1 = hexstr_to_bytes(
        "000000000000000000000000000000000000000000000000"
         "22f1a46c3c3496423c394621a2a5a8cf275b08")
    assert events == [("m.add_message", "phase1", enc1)]
    events[:] = []

    nonce2 = b"\x02" * SecretBox.NONCE_SIZE
    with mock.patch("nacl.utils.random", side_effect=[nonce2]) as r:
        s.send("phase2", b"msg")
    assert r.mock_calls == [mock.call(SecretBox.NONCE_SIZE)]
    enc2 = hexstr_to_bytes(
        "0202020202020202020202020202020202020202"
         "020202026660337c3eac6513c0dac9818b62ef16d9cd7e")
    assert events == [("m.add_message", "phase2", enc2)]

def test_key_first():
    s, m, events = build_send()
    key = b"\x00" * 32
    s.got_verified_key(key)
    assert events == []

    nonce1 = b"\x00" * SecretBox.NONCE_SIZE
    with mock.patch("nacl.utils.random", side_effect=[nonce1]) as r:
        s.send("phase1", b"msg")
    assert r.mock_calls == [mock.call(SecretBox.NONCE_SIZE)]
    enc1 = hexstr_to_bytes("00000000000000000000000000000000000000000000"
                            "000022f1a46c3c3496423c394621a2a5a8cf275b08")
    assert events == [("m.add_message", "phase1", enc1)]
    events[:] = []

    nonce2 = b"\x02" * SecretBox.NONCE_SIZE
    with mock.patch("nacl.utils.random", side_effect=[nonce2]) as r:
        s.send("phase2", b"msg")
    assert r.mock_calls == [mock.call(SecretBox.NONCE_SIZE)]
    enc2 = hexstr_to_bytes(
        "0202020202020202020202020202020202020"
         "202020202026660337c3eac6513c0dac9818b62ef16d9cd7e")
    assert events == [("m.add_message", "phase2", enc2)]


def build_order():
    events = []
    o = _order.Order("side", timing.DebugTiming())
    k = Dummy("k", events, IKey, "got_pake")
    r = Dummy("r", events, IReceive, "got_message")
    o.wire(k, r)
    return o, k, r, events


def test_in_order():
    o, k, r, events = build_order()
    o.got_message("side", "pake", b"body")
    assert events == [("k.got_pake", b"body")]  # right away
    o.got_message("side", "version", b"body")
    o.got_message("side", "1", b"body")
    assert events == [
        ("k.got_pake", b"body"),
        ("r.got_message", "side", "version", b"body"),
        ("r.got_message", "side", "1", b"body"),
    ]

def test_out_of_order():
    o, k, r, events = build_order()
    o.got_message("side", "version", b"body")
    assert events == []  # nothing yet
    o.got_message("side", "1", b"body")
    assert events == []  # nothing yet
    o.got_message("side", "pake", b"body")
    # got_pake is delivered first
    assert events == [
        ("k.got_pake", b"body"),
        ("r.got_message", "side", "version", b"body"),
        ("r.got_message", "side", "1", b"body"),
    ]


def build_receive():
    events = []
    r = _receive.Receive("side", timing.DebugTiming())
    b = Dummy("b", events, IBoss, "happy", "scared", "got_verifier",
              "got_message")
    s = Dummy("s", events, ISend, "got_verified_key")
    r.wire(b, s)
    return r, b, s, events


def test_good_receive():
    r, b, s, events = build_receive()
    key = b"key"
    r.got_key(key)
    assert events == []
    verifier = derive_key(key, b"wormhole:verifier")
    phase1_key = derive_phase_key(key, "side", "phase1")
    data1 = b"data1"
    good_body = encrypt_data(phase1_key, data1)
    r.got_message("side", "phase1", good_body)
    assert events == [
        ("s.got_verified_key", key),
        ("b.happy", ),
        ("b.got_verifier", verifier),
        ("b.got_message", "phase1", data1),
    ]

    phase2_key = derive_phase_key(key, "side", "phase2")
    data2 = b"data2"
    good_body = encrypt_data(phase2_key, data2)
    r.got_message("side", "phase2", good_body)
    assert events == [
        ("s.got_verified_key", key),
        ("b.happy", ),
        ("b.got_verifier", verifier),
        ("b.got_message", "phase1", data1),
        ("b.got_message", "phase2", data2),
    ]

def test_early_bad():
    r, b, s, events = build_receive()
    key = b"key"
    r.got_key(key)
    assert events == []
    phase1_key = derive_phase_key(key, "side", "bad")
    data1 = b"data1"
    bad_body = encrypt_data(phase1_key, data1)
    r.got_message("side", "phase1", bad_body)
    assert events == [
        ("b.scared", ),
    ]

    phase2_key = derive_phase_key(key, "side", "phase2")
    data2 = b"data2"
    good_body = encrypt_data(phase2_key, data2)
    r.got_message("side", "phase2", good_body)
    assert events == [
        ("b.scared", ),
    ]

def test_late_bad():
    r, b, s, events = build_receive()
    key = b"key"
    r.got_key(key)
    assert events == []
    verifier = derive_key(key, b"wormhole:verifier")
    phase1_key = derive_phase_key(key, "side", "phase1")
    data1 = b"data1"
    good_body = encrypt_data(phase1_key, data1)
    r.got_message("side", "phase1", good_body)
    assert events == [
        ("s.got_verified_key", key),
        ("b.happy", ),
        ("b.got_verifier", verifier),
        ("b.got_message", "phase1", data1),
    ]

    phase2_key = derive_phase_key(key, "side", "bad")
    data2 = b"data2"
    bad_body = encrypt_data(phase2_key, data2)
    r.got_message("side", "phase2", bad_body)
    assert events == [
        ("s.got_verified_key", key),
        ("b.happy", ),
        ("b.got_verifier", verifier),
        ("b.got_message", "phase1", data1),
        ("b.scared", ),
    ]
    r.got_message("side", "phase1", good_body)
    r.got_message("side", "phase2", bad_body)
    assert events == [
        ("s.got_verified_key", key),
        ("b.happy", ),
        ("b.got_verifier", verifier),
        ("b.got_message", "phase1", data1),
        ("b.scared", ),
    ]


def build_key():
    events = []
    k = _key.Key("appid", {}, "side", timing.DebugTiming())
    b = Dummy("b", events, IBoss, "scared", "got_key")
    m = Dummy("m", events, IMailbox, "add_message")
    r = Dummy("r", events, IReceive, "got_key")
    k.wire(b, m, r)
    return k, b, m, r, events


def test_good_key():
    k, b, m, r, events = build_key()
    code = "1-foo"
    k.got_code(code)
    assert len(events) == 1
    assert events[0][:2] == ("m.add_message", "pake")
    msg1_json = events[0][2].decode("utf-8")
    events[:] = []
    msg1 = json.loads(msg1_json)
    msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    key2 = sp.finish(msg1_bytes)
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
    k.got_pake(msg2)
    assert len(events) == 3, events
    assert events[0] == ("b.got_key", key2)
    assert events[1][:2] == ("m.add_message", "version")
    assert events[2] == ("r.got_key", key2)

def test_bad():
    k, b, m, r, events = build_key()
    code = "1-foo"
    k.got_code(code)
    assert len(events) == 1
    assert events[0][:2] == ("m.add_message", "pake")
    pake_1_json = events[0][2].decode("utf-8")
    pake_1 = json.loads(pake_1_json)
    assert list(pake_1.keys()) == \
                     ["pake_v1"]  # value is PAKE stuff
    events[:] = []
    bad_pake_d = {"not_pake_v1": "stuff"}
    k.got_pake(dict_to_bytes(bad_pake_d))
    assert events == [("b.scared", )]

def test_reversed():
    # A receiver using input_code() will choose the nameplate first, then
    # the rest of the code. Once the nameplate is selected, we'll claim
    # it and open the mailbox, which will cause the senders PAKE to
    # arrive before the code has been set. Key() is supposed to stash the
    # PAKE message until the code is set (allowing the PAKE computation
    # to finish). This test exercises that PAKE-then-code sequence.
    k, b, m, r, events = build_key()
    code = "1-foo"

    sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes("appid"))
    msg2_bytes = sp.start()
    msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
    k.got_pake(msg2)
    assert len(events) == 0

    k.got_code(code)
    assert len(events) == 4
    assert events[0][:2] == ("m.add_message", "pake")
    msg1_json = events[0][2].decode("utf-8")
    msg1 = json.loads(msg1_json)
    msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
    key2 = sp.finish(msg1_bytes)
    assert events[1] == ("b.got_key", key2)
    assert events[2][:2] == ("m.add_message", "version")
    assert events[3] == ("r.got_key", key2)


def build_code():
    events = []
    c = _code.Code(timing.DebugTiming())
    b = Dummy("b", events, IBoss, "got_code")
    a = Dummy("a", events, IAllocator, "allocate")
    n = Dummy("n", events, INameplate, "set_nameplate")
    k = Dummy("k", events, IKey, "got_code")
    i = Dummy("i", events, IInput, "start")
    c.wire(b, a, n, k, i)
    return c, b, a, n, k, i, events


def test_set_code():
    c, b, a, n, k, i, events = build_code()
    c.set_code("1-code")
    assert events == [
        ("n.set_nameplate", "1"),
        ("b.got_code", "1-code"),
        ("k.got_code", "1-code"),
    ]

def test_set_code_invalid():
    c, b, a, n, k, i, events = build_code()
    with pytest.raises(errors.KeyFormatError) as e:
        c.set_code("1-code ")
    assert str(e.value) == "Code '1-code ' contains spaces."
    with pytest.raises(errors.KeyFormatError) as e:
        c.set_code(" 1-code")
    assert str(e.value) == "Code ' 1-code' contains spaces."
    with pytest.raises(errors.KeyFormatError) as e:
        c.set_code("code-code")
    assert str(e.value) == \
        "Nameplate 'code' must be numeric, with no spaces."

    # it should still be possible to use the wormhole at this point
    c.set_code("1-code")
    assert events == [
        ("n.set_nameplate", "1"),
        ("b.got_code", "1-code"),
        ("k.got_code", "1-code"),
    ]

def test_allocate_code():
    c, b, a, n, k, i, events = build_code()
    wl = FakeWordList()
    c.allocate_code(2, wl)
    assert events == [("a.allocate", 2, wl)]
    events[:] = []
    c.allocated("1", "1-code")
    assert events == [
        ("n.set_nameplate", "1"),
        ("b.got_code", "1-code"),
        ("k.got_code", "1-code"),
    ]

def test_input_code():
    c, b, a, n, k, i, events = build_code()
    c.input_code()
    assert events == [("i.start", )]
    events[:] = []
    c.got_nameplate("1")
    assert events == [
        ("n.set_nameplate", "1"),
    ]
    events[:] = []
    c.finished_input("1-code")
    assert events == [
        ("b.got_code", "1-code"),
        ("k.got_code", "1-code"),
    ]


def build_input():
    events = []
    i = _input.Input(timing.DebugTiming())
    code = Dummy("c", events, ICode, "got_nameplate", "finished_input")
    lister = Dummy("l", events, ILister, "refresh")
    i.wire(code, lister)
    return i, code, lister, events


def test_ignore_completion():
    i, c, lister, events = build_input()
    helper = i.start()
    assert isinstance(helper, _input.Helper)
    assert events == [("l.refresh", )]
    events[:] = []
    with pytest.raises(errors.MustChooseNameplateFirstError):
        helper.choose_words("word-word")
    helper.choose_nameplate("1")
    assert events == [("c.got_nameplate", "1")]
    events[:] = []
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.choose_nameplate("2")
    helper.choose_words("word-word")
    with pytest.raises(errors.AlreadyChoseWordsError):
        helper.choose_words("word-word")
    assert events == [("c.finished_input", "1-word-word")]

def test_bad_nameplate():
    i, c, lister, events = build_input()
    helper = i.start()
    assert isinstance(helper, _input.Helper)
    assert events == [("l.refresh", )]
    events[:] = []
    with pytest.raises(errors.MustChooseNameplateFirstError):
        helper.choose_words("word-word")
    with pytest.raises(errors.KeyFormatError):
        helper.choose_nameplate(" 1")
    # should still work afterwards
    helper.choose_nameplate("1")
    assert events == [("c.got_nameplate", "1")]
    events[:] = []
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.choose_nameplate("2")
    helper.choose_words("word-word")
    with pytest.raises(errors.AlreadyChoseWordsError):
        helper.choose_words("word-word")
    assert events == [("c.finished_input", "1-word-word")]


@pytest_twisted.ensureDeferred
async def test_with_completion():
    i, c, lister, events = build_input()
    helper = i.start()
    assert isinstance(helper, _input.Helper)
    assert events == [("l.refresh", )]
    events[:] = []
    d = helper.when_wordlist_is_available()
    assert not d.called
    helper.refresh_nameplates()
    assert events == [("l.refresh", )]
    events[:] = []
    with pytest.raises(errors.MustChooseNameplateFirstError):
        helper.get_word_completions("prefix")
    i.got_nameplates({"1", "12", "34", "35", "367"})
    assert not d.called
    assert helper.get_nameplate_completions("") == \
        {"1-", "12-", "34-", "35-", "367-"}
    assert helper.get_nameplate_completions("1") == {"1-", "12-"}
    assert helper.get_nameplate_completions("2") == set()
    assert helper.get_nameplate_completions("3") == {"34-", "35-", "367-"}
    helper.choose_nameplate("34")
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.refresh_nameplates()
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.get_nameplate_completions("1")
    assert events == [("c.got_nameplate", "34")]
    events[:] = []
    # no wordlist yet
    assert not d.called
    assert helper.get_word_completions("") == set()
    wl = FakeWordList()
    i.got_wordlist(wl)
    assert await d is None
    # a new Deferred should fire right away
    d = helper.when_wordlist_is_available()
    assert await d is None

    wl._completions = {"abc-", "abcd-", "ae-"}
    assert helper.get_word_completions("a") == wl._completions
    assert wl._get_completions_prefix == "a"
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.refresh_nameplates()
    with pytest.raises(errors.AlreadyChoseNameplateError):
        helper.get_nameplate_completions("1")
    helper.choose_words("word-word")
    with pytest.raises(errors.AlreadyChoseWordsError):
        helper.get_word_completions("prefix")
    with pytest.raises(errors.AlreadyChoseWordsError):
        helper.choose_words("word-word")
    assert events == [("c.finished_input", "34-word-word")]


def build_lister():
    events = []
    lister = _lister.Lister(timing.DebugTiming())
    rc = Dummy("rc", events, IRendezvousConnector, "tx_list")
    i = Dummy("i", events, IInput, "got_nameplates")
    lister.wire(rc, i)
    return lister, rc, i, events


def test_connect_first_lister():
    lister, rc, i, events = build_lister()
    lister.connected()
    lister.lost()
    lister.connected()
    assert events == []
    lister.refresh()
    assert events == [
        ("rc.tx_list", ),
    ]
    events[:] = []
    lister.rx_nameplates({"1", "2", "3"})
    assert events == [
        ("i.got_nameplates", {"1", "2", "3"}),
    ]
    events[:] = []
    # now we're satisfied: disconnecting and reconnecting won't ask again
    lister.lost()
    lister.connected()
    assert events == []

    # but if we're told to refresh, we'll do so
    lister.refresh()
    assert events == [
        ("rc.tx_list", ),
    ]

def test_connect_first_ask_twice():
    lister, rc, i, events = build_lister()
    lister.connected()
    assert events == []
    lister.refresh()
    lister.refresh()
    assert events == [
        ("rc.tx_list", ),
        ("rc.tx_list", ),
    ]
    lister.rx_nameplates({"1", "2", "3"})
    assert events == [
        ("rc.tx_list", ),
        ("rc.tx_list", ),
        ("i.got_nameplates", {"1", "2", "3"}),
    ]
    lister.rx_nameplates({"1", "2", "3", "4"})
    assert events == [
        ("rc.tx_list", ),
        ("rc.tx_list", ),
        ("i.got_nameplates", {"1", "2", "3"}),
        ("i.got_nameplates", {"1", "2", "3", "4"}),
    ]

def test_reconnect():
    lister, rc, i, events = build_lister()
    lister.refresh()
    lister.connected()
    assert events == [
        ("rc.tx_list", ),
    ]
    events[:] = []
    lister.lost()
    lister.connected()
    assert events == [
        ("rc.tx_list", ),
    ]

def test_refresh_first():
    lister, rc, i, events = build_lister()
    lister.refresh()
    assert events == []
    lister.connected()
    assert events == [
        ("rc.tx_list", ),
    ]
    lister.rx_nameplates({"1", "2", "3"})
    assert events == [
        ("rc.tx_list", ),
        ("i.got_nameplates", {"1", "2", "3"}),
    ]

def test_unrefreshed():
    lister, rc, i, events = build_lister()
    assert events == []
    # we receive a spontaneous rx_nameplates, without asking
    lister.connected()
    assert events == []
    lister.rx_nameplates({"1", "2", "3"})
    assert events == [
        ("i.got_nameplates", {"1", "2", "3"}),
    ]


def build_allocator():
    events = []
    a = _allocator.Allocator(timing.DebugTiming())
    rc = Dummy("rc", events, IRendezvousConnector, "tx_allocate")
    c = Dummy("c", events, ICode, "allocated")
    a.wire(rc, c)
    return a, rc, c, events


def test_no_allocation():
    a, rc, c, events = build_allocator()
    a.connected()
    assert events == []

def test_allocate_first():
    a, rc, c, events = build_allocator()
    a.allocate(2, FakeWordList())
    assert events == []
    a.connected()
    assert events == [("rc.tx_allocate", )]
    events[:] = []
    a.lost()
    a.connected()
    assert events == [
        ("rc.tx_allocate", ),
    ]
    events[:] = []
    a.rx_allocated("1")
    assert events == [
        ("c.allocated", "1", "1-word-word"),
    ]

def test_connect_first_allocator():
    a, rc, c, events = build_allocator()
    a.connected()
    assert events == []
    a.allocate(2, FakeWordList())
    assert events == [("rc.tx_allocate", )]
    events[:] = []
    a.lost()
    a.connected()
    assert events == [
        ("rc.tx_allocate", ),
    ]
    events[:] = []
    a.rx_allocated("1")
    assert events == [
        ("c.allocated", "1", "1-word-word"),
    ]


def build_nameplate():
    events = []
    n = _nameplate.Nameplate(lambda **kw: None)
    m = Dummy("m", events, IMailbox, "got_mailbox")
    i = Dummy("i", events, IInput, "got_wordlist")
    rc = Dummy("rc", events, IRendezvousConnector, "tx_claim",
               "tx_release")
    t = Dummy("t", events, ITerminator, "nameplate_done")
    n.wire(m, i, rc, t)
    return n, m, i, rc, t, events


def test_set_invalid():
    n, m, i, rc, t, events = build_nameplate()
    with pytest.raises(errors.KeyFormatError) as e:
        n.set_nameplate(" 1")
    assert str(e.value) == \
        "Nameplate ' 1' must be numeric, with no spaces."
    with pytest.raises(errors.KeyFormatError) as e:
        n.set_nameplate("one")
    assert str(e.value) == \
        "Nameplate 'one' must be numeric, with no spaces."

    # wormhole should still be usable
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]

def test_set_first():
    # connection remains up throughout
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_connect_first_nameplate():
    # connection remains up throughout
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_reconnect_while_claiming():
    # connection bounced while waiting for rx_claimed
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    n.lost()
    n.connected()
    assert events == [("rc.tx_claim", "1")]

def test_reconnect_while_claimed():
    # connection bounced while claimed: no retransmits should be sent
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.lost()
    n.connected()
    assert events == []

def test_reconnect_while_releasing():
    # connection bounced while waiting for rx_released
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.lost()
    n.connected()
    assert events == [("rc.tx_release", "1")]

def test_reconnect_while_done():
    # connection bounces after we're done
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]
    events[:] = []

    n.lost()
    n.connected()
    assert events == []


def test_close_while_idle_nameplate():
    n, m, i, rc, t, events = build_nameplate()
    n.close()
    assert events == [("t.nameplate_done", )]


def test_close_while_idle_connected():
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []
    n.close()
    assert events == [("t.nameplate_done", )]


def test_close_while_unclaimed():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    n.close()  # before ever being connected
    assert events == [("t.nameplate_done", )]

def test_close_while_claiming():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    n.close()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_claiming_but_disconnected():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    n.lost()
    n.close()
    assert events == []
    # we're now waiting for a connection, so we can release the nameplate
    n.connected()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_claimed():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.close()
    # this path behaves just like a deliberate release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_claimed_but_disconnected():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.lost()
    n.close()
    # we're now waiting for a connection, so we can release the nameplate
    n.connected()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_releasing():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.close()  # ignored, we're already on our way out the door
    assert events == []
    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_releasing_but_disconnecteda():
    n, m, i, rc, t, events = build_nameplate()
    n.set_nameplate("1")
    assert events == []
    n.connected()
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.lost()
    n.close()
    # we must retransmit the tx_release when we reconnect
    assert events == []

    n.connected()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]

def test_close_while_done():
    # connection remains up throughout
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]
    events[:] = []

    n.close()  # NOP
    assert events == []

def test_close_while_done_but_disconnected():
    # connection remains up throughout
    n, m, i, rc, t, events = build_nameplate()
    n.connected()
    assert events == []

    n.set_nameplate("1")
    assert events == [("rc.tx_claim", "1")]
    events[:] = []

    wl = object()
    with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
        n.rx_claimed("mbox1")
    assert events == [
        ("i.got_wordlist", wl),
        ("m.got_mailbox", "mbox1"),
    ]
    events[:] = []

    n.release()
    assert events == [("rc.tx_release", "1")]
    events[:] = []

    n.rx_released()
    assert events == [("t.nameplate_done", )]
    events[:] = []

    n.lost()
    n.close()  # NOP
    assert events == []


def build_mailbox():
    events = []
    m = _mailbox.Mailbox("side1")
    n = Dummy("n", events, INameplate, "release")
    rc = Dummy("rc", events, IRendezvousConnector, "tx_add", "tx_open",
               "tx_close")
    o = Dummy("o", events, IOrder, "got_message")
    t = Dummy("t", events, ITerminator, "mailbox_done")
    m.wire(n, rc, o, t)
    return m, n, rc, o, t, events

    # TODO: test moods

def assert_events(events, initial_events, tx_add_events):
    assert len(events) == \
        len(initial_events) + len(tx_add_events), events
    assert events[:len(initial_events)] == initial_events
    assert set(events[len(initial_events):]) == tx_add_events


def test_connect_first_mailbox():  # connect before got_mailbox
    m, n, rc, o, t, events = build_mailbox()
    m.add_message("phase1", b"msg1")
    assert events == []

    m.connected()
    assert events == []

    m.got_mailbox("mbox1")
    assert events == [("rc.tx_open", "mbox1"),
                              ("rc.tx_add", "phase1", b"msg1")]
    events[:] = []

    m.add_message("phase2", b"msg2")
    assert events == [("rc.tx_add", "phase2", b"msg2")]
    events[:] = []

    # bouncing the connection should retransmit everything, even the open()
    m.lost()
    assert events == []
    # and messages sent while here should be queued
    m.add_message("phase3", b"msg3")
    assert events == []

    m.connected()
    # the other messages are allowed to be sent in any order
    assert_events(
        events, [("rc.tx_open", "mbox1")], {
            ("rc.tx_add", "phase1", b"msg1"),
            ("rc.tx_add", "phase2", b"msg2"),
            ("rc.tx_add", "phase3", b"msg3"),
        })
    events[:] = []

    m.rx_message("side1", "phase1",
                 b"msg1")  # echo of our message, dequeue
    assert events == []

    m.lost()
    m.connected()
    assert_events(events, [("rc.tx_open", "mbox1")], {
        ("rc.tx_add", "phase2", b"msg2"),
        ("rc.tx_add", "phase3", b"msg3"),
    })
    events[:] = []

    # a new message from the peer gets delivered, and the Nameplate is
    # released since the message proves that our peer opened the Mailbox
    # and therefore no longer needs the Nameplate
    m.rx_message("side2", "phase1", b"msg1them")  # new message from peer
    assert events == [
        ("n.release", ),
        ("o.got_message", "side2", "phase1", b"msg1them"),
    ]
    events[:] = []

    # we de-duplicate peer messages, but still re-release the nameplate
    # since Nameplate is smart enough to ignore that
    m.rx_message("side2", "phase1", b"msg1them")
    assert events == [
        ("n.release", ),
    ]
    events[:] = []

    m.close("happy")
    assert events == [("rc.tx_close", "mbox1", "happy")]
    events[:] = []

    # while closing, we ignore a lot
    m.add_message("phase-late", b"late")
    m.rx_message("side1", "phase2", b"msg2")
    m.close("happy")
    assert events == []

    # bouncing the connection forces a retransmit of the tx_close
    m.lost()
    assert events == []
    m.connected()
    assert events == [("rc.tx_close", "mbox1", "happy")]
    events[:] = []

    m.rx_closed()
    assert events == [("t.mailbox_done", )]
    events[:] = []

    # while closed, we ignore everything
    m.add_message("phase-late", b"late")
    m.rx_message("side1", "phase2", b"msg2")
    m.close("happy")
    m.lost()
    m.connected()
    assert events == []

def test_mailbox_first():  # got_mailbox before connect
    m, n, rc, o, t, events = build_mailbox()
    m.add_message("phase1", b"msg1")
    assert events == []

    m.got_mailbox("mbox1")
    m.add_message("phase2", b"msg2")
    assert events == []

    m.connected()

    assert_events(events, [("rc.tx_open", "mbox1")], {
        ("rc.tx_add", "phase1", b"msg1"),
        ("rc.tx_add", "phase2", b"msg2"),
    })


def test_close_while_idle():
    m, n, rc, o, t, events = build_mailbox()
    m.close("happy")
    assert events == [("t.mailbox_done", )]


def test_close_while_idle_but_connected():
    m, n, rc, o, t, events = build_mailbox()
    m.connected()
    m.close("happy")
    assert events == [("t.mailbox_done", )]

def test_close_while_mailbox_disconnected():
    m, n, rc, o, t, events = build_mailbox()
    m.got_mailbox("mbox1")
    m.close("happy")
    assert events == [("t.mailbox_done", )]

def test_close_while_reconnecting():
    m, n, rc, o, t, events = build_mailbox()
    m.got_mailbox("mbox1")
    m.connected()
    assert events == [("rc.tx_open", "mbox1")]
    events[:] = []

    m.lost()
    assert events == []
    m.close("happy")
    assert events == []
    # we now wait to connect, so we can send the tx_close

    m.connected()
    assert events == [("rc.tx_close", "mbox1", "happy")]
    events[:] = []

    m.rx_closed()
    assert events == [("t.mailbox_done", )]
    events[:] = []


def build_terminator():
    events = []
    t = _terminator.Terminator()
    b = Dummy("b", events, IBoss, "closed")
    rc = Dummy("rc", events, IRendezvousConnector, "stop")
    n = Dummy("n", events, INameplate, "close")
    m = Dummy("m", events, IMailbox, "close")
    d = Dummy("d", events, IDilator, "stop")
    t.wire(b, rc, n, m, d)
    return t, b, rc, n, m, events


# there are three events, and we need to test all orderings of them
def _do_test(ev1, ev2, ev3):
    t, b, rc, n, m, events = build_terminator()
    input_events = {
        "mailbox": lambda: t.mailbox_done(),
        "nameplate": lambda: t.nameplate_done(),
        "rc": lambda: t.close("happy"),
    }
    close_events = [
        ("n.close", ),
        ("m.close", "happy"),
    ]

    if ev1 == "mailbox":
        close_events.remove(("m.close", "happy"))
    elif ev1 == "nameplate":
        close_events.remove(("n.close",))

    input_events[ev1]()
    expected = []
    if ev1 == "rc":
        expected.extend(close_events)
    assert events == expected
    events[:] = []

    if ev2 == "mailbox":
        close_events.remove(("m.close", "happy"))
    elif ev2 == "nameplate":
        close_events.remove(("n.close",))

    input_events[ev2]()
    expected = []
    if ev2 == "rc":
        expected.extend(close_events)
    assert events == expected
    events[:] = []

    if ev3 == "mailbox":
        close_events.remove(("m.close", "happy"))
    elif ev3 == "nameplate":
        close_events.remove(("n.close",))

    input_events[ev3]()
    expected = []
    if ev3 == "rc":
        expected.extend(close_events)
    expected.append(("rc.stop", ))
    assert events == expected
    events[:] = []

    t.stoppedRC()
    assert events == [("d.stop", )]
    events[:] = []

    t.stoppedD()
    assert events == [("b.closed", )]

def test_terminate():
    _do_test("mailbox", "nameplate", "rc")
    _do_test("mailbox", "rc", "nameplate")
    _do_test("nameplate", "mailbox", "rc")
    _do_test("nameplate", "rc", "mailbox")
    _do_test("rc", "nameplate", "mailbox")
    _do_test("rc", "mailbox", "nameplate")


# TODO: test moods


class MockBoss(_boss.Boss):
    def __attrs_post_init__(self):
        self._current_wormhole_status = WormholeStatus()
        # self._build_workers()
        self._init_other_state()


def build_boss():
    events = []
    wormhole = Dummy("w", events, None, "got_welcome", "got_code",
                     "got_key", "got_verifier", "got_versions", "received",
                     "closed")
    versions = {"app": "version1"}
    reactor = None
    eq = None
    cooperator = None
    journal = ImmediateJournal()
    tor_manager = None
    client_version = ("python", __version__)
    b = MockBoss(wormhole, "side", "url", "appid", versions,
                 client_version, reactor, eq, cooperator, journal,
                 tor_manager,
                 timing.DebugTiming())
    b._T = Dummy("t", events, ITerminator, "close")
    b._S = Dummy("s", events, ISend, "send")
    b._RC = Dummy("rc", events, IRendezvousConnector, "start")
    b._C = Dummy("c", events, ICode, "allocate_code", "input_code",
                 "set_code")
    b._D = Dummy("d", events, IDilator, "got_wormhole_versions", "got_key", _manager=None)
    return b, events

def test_boss_basic():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.got_code("1-code")
    assert events == [("w.got_code", "1-code")]
    events[:] = []

    welcome = {"howdy": "how are ya"}
    b.rx_welcome(welcome)
    assert events == [
        ("w.got_welcome", welcome),
    ]
    events[:] = []

    # pretend a peer message was correctly decrypted
    b.got_key(b"key")
    b.happy()
    b.got_verifier(b"verifier")
    b.got_message("version", b"{}")
    b.got_message("0", b"msg1")
    assert events == [
        ("w.got_key", b"key"),
        ("d.got_key", b"key"),
        ("w.got_verifier", b"verifier"),
        ("d.got_wormhole_versions", {}),
        ("w.got_versions", {}),
        ("w.received", b"msg1"),
    ]
    events[:] = []

    b.send(b"msg2")
    assert events == [("s.send", "0", b"msg2")]
    events[:] = []

    b.close()
    assert events == [("t.close", "happy")]
    events[:] = []

    b.closed()
    assert events == [("w.closed", "happy")]

def test_unwelcome():
    b, events = build_boss()
    unwelcome = {"error": "go away"}
    b.rx_welcome(unwelcome)
    assert events == [("t.close", "unwelcome")]

def test_lonely():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.got_code("1-code")
    assert events == [("w.got_code", "1-code")]
    events[:] = []

    b.close()
    assert events == [("t.close", "lonely")]
    events[:] = []

    b.closed()
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], errors.LonelyError)

def test_server_error():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    orig = {}
    b.rx_error("server-error-msg", orig)
    assert events == [("t.close", "errory")]
    events[:] = []

    b.closed()
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], errors.ServerError)
    assert events[0][1].args[0] == "server-error-msg"

def test_internal_error():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.error(ValueError("catch me"))
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], ValueError)
    assert events[0][1].args[0] == "catch me"

def test_close_early():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.close()  # before even w.got_code
    assert events == [("t.close", "lonely")]
    events[:] = []

    b.closed()
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], errors.LonelyError)

def test_error_while_closing():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.close()
    assert events == [("t.close", "lonely")]
    events[:] = []

    b.error(ValueError("oops"))
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], ValueError)

def test_scary_version():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.got_code("1-code")
    assert events == [("w.got_code", "1-code")]
    events[:] = []

    b.scared()
    assert events == [("t.close", "scary")]
    events[:] = []

    b.closed()
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], errors.WrongPasswordError)

def test_scary_phase():
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.got_code("1-code")
    assert events == [("w.got_code", "1-code")]
    events[:] = []

    b.happy()  # phase=version

    b.scared()  # phase=0
    assert events == [("t.close", "scary")]
    events[:] = []

    b.closed()
    assert len(events) == 1, events
    assert events[0][0] == "w.closed"
    assert isinstance(events[0][1], errors.WrongPasswordError)


def test_unknown_phase(observe_errors):
    b, events = build_boss()
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]
    events[:] = []

    b.got_code("1-code")
    assert events == [("w.got_code", "1-code")]
    events[:] = []

    b.happy()  # phase=version

    b.got_message("unknown-phase", b"spooky")
    assert events == []
    observe_errors.flush(errors._UnknownPhaseError)


def test_set_code_bad_format():
    b, events = build_boss()
    with pytest.raises(errors.KeyFormatError):
        b.set_code("1 code")
    # wormhole should still be usable
    b.set_code("1-code")
    assert events == [("c.set_code", "1-code")]


def test_set_code_twice():
    b, events = build_boss()
    b.set_code("1-code")
    with pytest.raises(errors.OnlyOneCodeError):
        b.set_code("1-code")


def test_input_code_boss():
    b, events = build_boss()
    b._C.retval = "helper"
    helper = b.input_code()
    assert events == [("c.input_code", )]
    assert helper == "helper"
    with pytest.raises(errors.OnlyOneCodeError):
        b.input_code()


def test_allocate_code_boss():
    b, events = build_boss()
    wl = object()
    with mock.patch("wormhole._boss.PGPWordList", return_value=wl):
        b.allocate_code(3)
    assert events == [("c.allocate_code", 3, wl)]
    with pytest.raises(errors.OnlyOneCodeError):
        b.allocate_code(3)


def build_rendezvous():
    events = []
    reactor = object()
    journal = ImmediateJournal()
    tor_manager = None
    client_version = ("python", __version__)
    rc = _rendezvous.RendezvousConnector(
        "ws://host:4000/v1", "appid", "side", reactor, journal,
        tor_manager, timing.DebugTiming(), client_version, lambda **kw: None)
    b = Dummy("b", events, IBoss, "error")
    n = Dummy("n", events, INameplate, "connected", "lost")
    m = Dummy("m", events, IMailbox, "connected", "lost")
    a = Dummy("a", events, IAllocator, "connected", "lost")
    x = Dummy("l", events, ILister, "connected", "lost")
    t = Dummy("t", events, ITerminator)
    rc.wire(b, n, m, a, x, t)
    return rc, events


def test_rendezvous_basic():
    rc, events = build_rendezvous()
    del rc, events

def test_websocket_failure():
    # if the TCP connection succeeds, but the subsequent WebSocket
    # negotiation fails, then we'll see an onClose without first seeing
    # onOpen
    rc, events = build_rendezvous()
    rc.ws_close(False, 1006, "connection was closed uncleanly")
    # this should cause the ClientService to be shut down, and an error
    # delivered to the Boss
    assert len(events) == 1, events
    assert events[0][0] == "b.error"
    assert isinstance(events[0][1], errors.ServerConnectionError)
    assert str(events[0][1]) == "connection was closed uncleanly"

def test_websocket_lost():
    # if the TCP connection succeeds, and negotiation completes, then the
    # connection is lost, several machines should be notified
    rc, events = build_rendezvous()

    ws = mock.Mock()

    def notrandom(length):
        return b"\x00" * length

    with mock.patch("os.urandom", notrandom):
        rc.ws_open(ws)
    assert events == [
        ("n.connected", ),
        ("m.connected", ),
        ("l.connected", ),
        ("a.connected", ),
    ]
    events[:] = []

    def sent_messages(ws):
        for c in ws.mock_calls:
            assert c[0] == "sendMessage", ws.mock_calls
            assert not c[1][1]
            yield bytes_to_dict(c[1][0])

    assert list(sent_messages(ws)) == [
            dict(
                appid="appid",
                side="side",
                client_version=["python", __version__],
                id="0000",
                type="bind"),
        ]

    rc.ws_close(True, None, None)
    assert events == [
        ("n.lost", ),
        ("m.lost", ),
        ("l.lost", ),
        ("a.lost", ),
    ]

def test_endpoints():
    # parse different URLs and check the tls status of each
    reactor = object()
    journal = ImmediateJournal()
    tor_manager = None
    client_version = ("python", __version__)
    rc = _rendezvous.RendezvousConnector(
        "ws://host:4000/v1", "appid", "side", reactor, journal,
        tor_manager, timing.DebugTiming(), client_version, lambda **kw: None)

    new_ep = object()
    with mock.patch("twisted.internet.endpoints.HostnameEndpoint",
                    return_value=new_ep) as he:
        ep = rc._make_endpoint("ws://host:4000/v1")
    assert he.mock_calls == [mock.call(reactor, "host", 4000)]
    assert ep is new_ep

    new_ep = object()
    with mock.patch("twisted.internet.endpoints.HostnameEndpoint",
                    return_value=new_ep) as he:
        ep = rc._make_endpoint("ws://host/v1")
    assert he.mock_calls == [mock.call(reactor, "host", 80)]
    assert ep is new_ep

    new_ep = object()
    with mock.patch("twisted.internet.endpoints.clientFromString",
                    return_value=new_ep) as cfs:
        ep = rc._make_endpoint("wss://host:4000/v1")
    assert cfs.mock_calls == [mock.call(reactor, "tls:host:4000")]
    assert ep is new_ep

    new_ep = object()
    with mock.patch("twisted.internet.endpoints.clientFromString",
                    return_value=new_ep) as cfs:
        ep = rc._make_endpoint("wss://host/v1")
    assert cfs.mock_calls == [mock.call(reactor, "tls:host:443")]
    assert ep is new_ep

    tor_manager = mock.Mock()
    directlyProvides(tor_manager, ITorManager)
    rc = _rendezvous.RendezvousConnector(
        "ws://host:4000/v1", "appid", "side", reactor, journal,
        tor_manager, timing.DebugTiming(), client_version, lambda **kw: None)

    tor_manager.mock_calls[:] = []
    ep = rc._make_endpoint("ws://host:4000/v1")
    assert tor_manager.mock_calls == \
                     [mock.call.stream_via("host", 4000, tls=False)]

    tor_manager.mock_calls[:] = []
    ep = rc._make_endpoint("ws://host/v1")
    assert tor_manager.mock_calls == \
                     [mock.call.stream_via("host", 80, tls=False)]

    tor_manager.mock_calls[:] = []
    ep = rc._make_endpoint("wss://host:4000/v1")
    assert tor_manager.mock_calls == \
                     [mock.call.stream_via("host", 4000, tls=True)]

    tor_manager.mock_calls[:] = []
    ep = rc._make_endpoint("wss://host/v1")
    assert tor_manager.mock_calls == \
                     [mock.call.stream_via("host", 443, tls=True)]


# TODO
# #Send
# #Mailbox
# #Nameplate
# #Terminator
# Boss
# RendezvousConnector (not a state machine)
# #Input: exercise helper methods
# #wordlist
# test idempotency / at-most-once where applicable
