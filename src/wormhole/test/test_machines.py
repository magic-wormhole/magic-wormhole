from __future__ import print_function, unicode_literals

import json

from nacl.secret import SecretBox
from spake2 import SPAKE2_Symmetric
from twisted.trial import unittest
from zope.interface import directlyProvides, implementer

import mock

from .. import (__version__, _allocator, _boss, _code, _input, _key, _lister,
                _mailbox, _nameplate, _order, _receive, _rendezvous, _send,
                _terminator, errors, timing)
from .._interfaces import (IAllocator, IBoss, ICode, IInput, IKey, ILister,
                           IMailbox, INameplate, IOrder, IReceive,
                           IRendezvousConnector, ISend, ITerminator, IWordlist)
from .._key import derive_key, derive_phase_key, encrypt_data
from ..journal import ImmediateJournal
from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, to_bytes)


@implementer(IWordlist)
class FakeWordList(object):
    def choose_words(self, length):
        return "-".join(["word"] * length)

    def get_completions(self, prefix):
        self._get_completions_prefix = prefix
        return self._completions


class Dummy:
    def __init__(self, name, events, iface, *meths):
        self.name = name
        self.events = events
        if iface:
            directlyProvides(self, iface)
        for meth in meths:
            self.mock(meth)
        self.retval = None

    def mock(self, meth):
        def log(*args):
            self.events.append(("%s.%s" % (self.name, meth), ) + args)
            return self.retval

        setattr(self, meth, log)


class Send(unittest.TestCase):
    def build(self):
        events = []
        s = _send.Send(u"side", timing.DebugTiming())
        m = Dummy("m", events, IMailbox, "add_message")
        s.wire(m)
        return s, m, events

    def test_send_first(self):
        s, m, events = self.build()
        s.send("phase1", b"msg")
        self.assertEqual(events, [])
        key = b"\x00" * 32
        nonce1 = b"\x00" * SecretBox.NONCE_SIZE
        with mock.patch("nacl.utils.random", side_effect=[nonce1]) as r:
            s.got_verified_key(key)
        self.assertEqual(r.mock_calls, [mock.call(SecretBox.NONCE_SIZE)])
        # print(bytes_to_hexstr(events[0][2]))
        enc1 = hexstr_to_bytes(
            ("000000000000000000000000000000000000000000000000"
             "22f1a46c3c3496423c394621a2a5a8cf275b08"))
        self.assertEqual(events, [("m.add_message", "phase1", enc1)])
        events[:] = []

        nonce2 = b"\x02" * SecretBox.NONCE_SIZE
        with mock.patch("nacl.utils.random", side_effect=[nonce2]) as r:
            s.send("phase2", b"msg")
        self.assertEqual(r.mock_calls, [mock.call(SecretBox.NONCE_SIZE)])
        enc2 = hexstr_to_bytes(
            ("0202020202020202020202020202020202020202"
             "020202026660337c3eac6513c0dac9818b62ef16d9cd7e"))
        self.assertEqual(events, [("m.add_message", "phase2", enc2)])

    def test_key_first(self):
        s, m, events = self.build()
        key = b"\x00" * 32
        s.got_verified_key(key)
        self.assertEqual(events, [])

        nonce1 = b"\x00" * SecretBox.NONCE_SIZE
        with mock.patch("nacl.utils.random", side_effect=[nonce1]) as r:
            s.send("phase1", b"msg")
        self.assertEqual(r.mock_calls, [mock.call(SecretBox.NONCE_SIZE)])
        enc1 = hexstr_to_bytes(("00000000000000000000000000000000000000000000"
                                "000022f1a46c3c3496423c394621a2a5a8cf275b08"))
        self.assertEqual(events, [("m.add_message", "phase1", enc1)])
        events[:] = []

        nonce2 = b"\x02" * SecretBox.NONCE_SIZE
        with mock.patch("nacl.utils.random", side_effect=[nonce2]) as r:
            s.send("phase2", b"msg")
        self.assertEqual(r.mock_calls, [mock.call(SecretBox.NONCE_SIZE)])
        enc2 = hexstr_to_bytes(
            ("0202020202020202020202020202020202020"
             "202020202026660337c3eac6513c0dac9818b62ef16d9cd7e"))
        self.assertEqual(events, [("m.add_message", "phase2", enc2)])


class Order(unittest.TestCase):
    def build(self):
        events = []
        o = _order.Order(u"side", timing.DebugTiming())
        k = Dummy("k", events, IKey, "got_pake")
        r = Dummy("r", events, IReceive, "got_message")
        o.wire(k, r)
        return o, k, r, events

    def test_in_order(self):
        o, k, r, events = self.build()
        o.got_message(u"side", u"pake", b"body")
        self.assertEqual(events, [("k.got_pake", b"body")])  # right away
        o.got_message(u"side", u"version", b"body")
        o.got_message(u"side", u"1", b"body")
        self.assertEqual(events, [
            ("k.got_pake", b"body"),
            ("r.got_message", u"side", u"version", b"body"),
            ("r.got_message", u"side", u"1", b"body"),
        ])

    def test_out_of_order(self):
        o, k, r, events = self.build()
        o.got_message(u"side", u"version", b"body")
        self.assertEqual(events, [])  # nothing yet
        o.got_message(u"side", u"1", b"body")
        self.assertEqual(events, [])  # nothing yet
        o.got_message(u"side", u"pake", b"body")
        # got_pake is delivered first
        self.assertEqual(events, [
            ("k.got_pake", b"body"),
            ("r.got_message", u"side", u"version", b"body"),
            ("r.got_message", u"side", u"1", b"body"),
        ])


class Receive(unittest.TestCase):
    def build(self):
        events = []
        r = _receive.Receive(u"side", timing.DebugTiming())
        b = Dummy("b", events, IBoss, "happy", "scared", "got_verifier",
                  "got_message")
        s = Dummy("s", events, ISend, "got_verified_key")
        r.wire(b, s)
        return r, b, s, events

    def test_good(self):
        r, b, s, events = self.build()
        key = b"key"
        r.got_key(key)
        self.assertEqual(events, [])
        verifier = derive_key(key, b"wormhole:verifier")
        phase1_key = derive_phase_key(key, u"side", u"phase1")
        data1 = b"data1"
        good_body = encrypt_data(phase1_key, data1)
        r.got_message(u"side", u"phase1", good_body)
        self.assertEqual(events, [
            ("s.got_verified_key", key),
            ("b.happy", ),
            ("b.got_verifier", verifier),
            ("b.got_message", u"phase1", data1),
        ])

        phase2_key = derive_phase_key(key, u"side", u"phase2")
        data2 = b"data2"
        good_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", good_body)
        self.assertEqual(events, [
            ("s.got_verified_key", key),
            ("b.happy", ),
            ("b.got_verifier", verifier),
            ("b.got_message", u"phase1", data1),
            ("b.got_message", u"phase2", data2),
        ])

    def test_early_bad(self):
        r, b, s, events = self.build()
        key = b"key"
        r.got_key(key)
        self.assertEqual(events, [])
        phase1_key = derive_phase_key(key, u"side", u"bad")
        data1 = b"data1"
        bad_body = encrypt_data(phase1_key, data1)
        r.got_message(u"side", u"phase1", bad_body)
        self.assertEqual(events, [
            ("b.scared", ),
        ])

        phase2_key = derive_phase_key(key, u"side", u"phase2")
        data2 = b"data2"
        good_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", good_body)
        self.assertEqual(events, [
            ("b.scared", ),
        ])

    def test_late_bad(self):
        r, b, s, events = self.build()
        key = b"key"
        r.got_key(key)
        self.assertEqual(events, [])
        verifier = derive_key(key, b"wormhole:verifier")
        phase1_key = derive_phase_key(key, u"side", u"phase1")
        data1 = b"data1"
        good_body = encrypt_data(phase1_key, data1)
        r.got_message(u"side", u"phase1", good_body)
        self.assertEqual(events, [
            ("s.got_verified_key", key),
            ("b.happy", ),
            ("b.got_verifier", verifier),
            ("b.got_message", u"phase1", data1),
        ])

        phase2_key = derive_phase_key(key, u"side", u"bad")
        data2 = b"data2"
        bad_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", bad_body)
        self.assertEqual(events, [
            ("s.got_verified_key", key),
            ("b.happy", ),
            ("b.got_verifier", verifier),
            ("b.got_message", u"phase1", data1),
            ("b.scared", ),
        ])
        r.got_message(u"side", u"phase1", good_body)
        r.got_message(u"side", u"phase2", bad_body)
        self.assertEqual(events, [
            ("s.got_verified_key", key),
            ("b.happy", ),
            ("b.got_verifier", verifier),
            ("b.got_message", u"phase1", data1),
            ("b.scared", ),
        ])


class Key(unittest.TestCase):
    def build(self):
        events = []
        k = _key.Key(u"appid", {}, u"side", timing.DebugTiming())
        b = Dummy("b", events, IBoss, "scared", "got_key")
        m = Dummy("m", events, IMailbox, "add_message")
        r = Dummy("r", events, IReceive, "got_key")
        k.wire(b, m, r)
        return k, b, m, r, events

    def test_good(self):
        k, b, m, r, events = self.build()
        code = u"1-foo"
        k.got_code(code)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], ("m.add_message", "pake"))
        msg1_json = events[0][2].decode("utf-8")
        events[:] = []
        msg1 = json.loads(msg1_json)
        msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
        sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes(u"appid"))
        msg2_bytes = sp.start()
        key2 = sp.finish(msg1_bytes)
        msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
        k.got_pake(msg2)
        self.assertEqual(len(events), 3, events)
        self.assertEqual(events[0], ("b.got_key", key2))
        self.assertEqual(events[1][:2], ("m.add_message", "version"))
        self.assertEqual(events[2], ("r.got_key", key2))

    def test_bad(self):
        k, b, m, r, events = self.build()
        code = u"1-foo"
        k.got_code(code)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], ("m.add_message", "pake"))
        pake_1_json = events[0][2].decode("utf-8")
        pake_1 = json.loads(pake_1_json)
        self.assertEqual(list(pake_1.keys()),
                         ["pake_v1"])  # value is PAKE stuff
        events[:] = []
        bad_pake_d = {"not_pake_v1": "stuff"}
        k.got_pake(dict_to_bytes(bad_pake_d))
        self.assertEqual(events, [("b.scared", )])

    def test_reversed(self):
        # A receiver using input_code() will choose the nameplate first, then
        # the rest of the code. Once the nameplate is selected, we'll claim
        # it and open the mailbox, which will cause the senders PAKE to
        # arrive before the code has been set. Key() is supposed to stash the
        # PAKE message until the code is set (allowing the PAKE computation
        # to finish). This test exercises that PAKE-then-code sequence.
        k, b, m, r, events = self.build()
        code = u"1-foo"

        sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes(u"appid"))
        msg2_bytes = sp.start()
        msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
        k.got_pake(msg2)
        self.assertEqual(len(events), 0)

        k.got_code(code)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0][:2], ("m.add_message", "pake"))
        msg1_json = events[0][2].decode("utf-8")
        msg1 = json.loads(msg1_json)
        msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
        key2 = sp.finish(msg1_bytes)
        self.assertEqual(events[1], ("b.got_key", key2))
        self.assertEqual(events[2][:2], ("m.add_message", "version"))
        self.assertEqual(events[3], ("r.got_key", key2))


class Code(unittest.TestCase):
    def build(self):
        events = []
        c = _code.Code(timing.DebugTiming())
        b = Dummy("b", events, IBoss, "got_code")
        a = Dummy("a", events, IAllocator, "allocate")
        n = Dummy("n", events, INameplate, "set_nameplate")
        k = Dummy("k", events, IKey, "got_code")
        i = Dummy("i", events, IInput, "start")
        c.wire(b, a, n, k, i)
        return c, b, a, n, k, i, events

    def test_set_code(self):
        c, b, a, n, k, i, events = self.build()
        c.set_code(u"1-code")
        self.assertEqual(events, [
            ("n.set_nameplate", u"1"),
            ("b.got_code", u"1-code"),
            ("k.got_code", u"1-code"),
        ])

    def test_set_code_invalid(self):
        c, b, a, n, k, i, events = self.build()
        with self.assertRaises(errors.KeyFormatError) as e:
            c.set_code(u"1-code ")
        self.assertEqual(str(e.exception), "Code '1-code ' contains spaces.")
        with self.assertRaises(errors.KeyFormatError) as e:
            c.set_code(u" 1-code")
        self.assertEqual(str(e.exception), "Code ' 1-code' contains spaces.")
        with self.assertRaises(errors.KeyFormatError) as e:
            c.set_code(u"code-code")
        self.assertEqual(
            str(e.exception),
            "Nameplate 'code' must be numeric, with no spaces.")

        # it should still be possible to use the wormhole at this point
        c.set_code(u"1-code")
        self.assertEqual(events, [
            ("n.set_nameplate", u"1"),
            ("b.got_code", u"1-code"),
            ("k.got_code", u"1-code"),
        ])

    def test_allocate_code(self):
        c, b, a, n, k, i, events = self.build()
        wl = FakeWordList()
        c.allocate_code(2, wl)
        self.assertEqual(events, [("a.allocate", 2, wl)])
        events[:] = []
        c.allocated("1", "1-code")
        self.assertEqual(events, [
            ("n.set_nameplate", u"1"),
            ("b.got_code", u"1-code"),
            ("k.got_code", u"1-code"),
        ])

    def test_input_code(self):
        c, b, a, n, k, i, events = self.build()
        c.input_code()
        self.assertEqual(events, [("i.start", )])
        events[:] = []
        c.got_nameplate("1")
        self.assertEqual(events, [
            ("n.set_nameplate", u"1"),
        ])
        events[:] = []
        c.finished_input("1-code")
        self.assertEqual(events, [
            ("b.got_code", u"1-code"),
            ("k.got_code", u"1-code"),
        ])


class Input(unittest.TestCase):
    def build(self):
        events = []
        i = _input.Input(timing.DebugTiming())
        c = Dummy("c", events, ICode, "got_nameplate", "finished_input")
        l = Dummy("l", events, ILister, "refresh")
        i.wire(c, l)
        return i, c, l, events

    def test_ignore_completion(self):
        i, c, l, events = self.build()
        helper = i.start()
        self.assertIsInstance(helper, _input.Helper)
        self.assertEqual(events, [("l.refresh", )])
        events[:] = []
        with self.assertRaises(errors.MustChooseNameplateFirstError):
            helper.choose_words("word-word")
        helper.choose_nameplate("1")
        self.assertEqual(events, [("c.got_nameplate", "1")])
        events[:] = []
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.choose_nameplate("2")
        helper.choose_words("word-word")
        with self.assertRaises(errors.AlreadyChoseWordsError):
            helper.choose_words("word-word")
        self.assertEqual(events, [("c.finished_input", "1-word-word")])

    def test_bad_nameplate(self):
        i, c, l, events = self.build()
        helper = i.start()
        self.assertIsInstance(helper, _input.Helper)
        self.assertEqual(events, [("l.refresh", )])
        events[:] = []
        with self.assertRaises(errors.MustChooseNameplateFirstError):
            helper.choose_words("word-word")
        with self.assertRaises(errors.KeyFormatError):
            helper.choose_nameplate(" 1")
        # should still work afterwards
        helper.choose_nameplate("1")
        self.assertEqual(events, [("c.got_nameplate", "1")])
        events[:] = []
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.choose_nameplate("2")
        helper.choose_words("word-word")
        with self.assertRaises(errors.AlreadyChoseWordsError):
            helper.choose_words("word-word")
        self.assertEqual(events, [("c.finished_input", "1-word-word")])

    def test_with_completion(self):
        i, c, l, events = self.build()
        helper = i.start()
        self.assertIsInstance(helper, _input.Helper)
        self.assertEqual(events, [("l.refresh", )])
        events[:] = []
        d = helper.when_wordlist_is_available()
        self.assertNoResult(d)
        helper.refresh_nameplates()
        self.assertEqual(events, [("l.refresh", )])
        events[:] = []
        with self.assertRaises(errors.MustChooseNameplateFirstError):
            helper.get_word_completions("prefix")
        i.got_nameplates({"1", "12", "34", "35", "367"})
        self.assertNoResult(d)
        self.assertEqual(
            helper.get_nameplate_completions(""),
            {"1-", "12-", "34-", "35-", "367-"})
        self.assertEqual(helper.get_nameplate_completions("1"), {"1-", "12-"})
        self.assertEqual(helper.get_nameplate_completions("2"), set())
        self.assertEqual(
            helper.get_nameplate_completions("3"), {"34-", "35-", "367-"})
        helper.choose_nameplate("34")
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.refresh_nameplates()
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.get_nameplate_completions("1")
        self.assertEqual(events, [("c.got_nameplate", "34")])
        events[:] = []
        # no wordlist yet
        self.assertNoResult(d)
        self.assertEqual(helper.get_word_completions(""), set())
        wl = FakeWordList()
        i.got_wordlist(wl)
        self.assertEqual(self.successResultOf(d), None)
        # a new Deferred should fire right away
        d = helper.when_wordlist_is_available()
        self.assertEqual(self.successResultOf(d), None)

        wl._completions = {"abc-", "abcd-", "ae-"}
        self.assertEqual(helper.get_word_completions("a"), wl._completions)
        self.assertEqual(wl._get_completions_prefix, "a")
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.refresh_nameplates()
        with self.assertRaises(errors.AlreadyChoseNameplateError):
            helper.get_nameplate_completions("1")
        helper.choose_words("word-word")
        with self.assertRaises(errors.AlreadyChoseWordsError):
            helper.get_word_completions("prefix")
        with self.assertRaises(errors.AlreadyChoseWordsError):
            helper.choose_words("word-word")
        self.assertEqual(events, [("c.finished_input", "34-word-word")])


class Lister(unittest.TestCase):
    def build(self):
        events = []
        lister = _lister.Lister(timing.DebugTiming())
        rc = Dummy("rc", events, IRendezvousConnector, "tx_list")
        i = Dummy("i", events, IInput, "got_nameplates")
        lister.wire(rc, i)
        return lister, rc, i, events

    def test_connect_first(self):
        l, rc, i, events = self.build()
        l.connected()
        l.lost()
        l.connected()
        self.assertEqual(events, [])
        l.refresh()
        self.assertEqual(events, [
            ("rc.tx_list", ),
        ])
        events[:] = []
        l.rx_nameplates({"1", "2", "3"})
        self.assertEqual(events, [
            ("i.got_nameplates", {"1", "2", "3"}),
        ])
        events[:] = []
        # now we're satisfied: disconnecting and reconnecting won't ask again
        l.lost()
        l.connected()
        self.assertEqual(events, [])

        # but if we're told to refresh, we'll do so
        l.refresh()
        self.assertEqual(events, [
            ("rc.tx_list", ),
        ])

    def test_connect_first_ask_twice(self):
        l, rc, i, events = self.build()
        l.connected()
        self.assertEqual(events, [])
        l.refresh()
        l.refresh()
        self.assertEqual(events, [
            ("rc.tx_list", ),
            ("rc.tx_list", ),
        ])
        l.rx_nameplates({"1", "2", "3"})
        self.assertEqual(events, [
            ("rc.tx_list", ),
            ("rc.tx_list", ),
            ("i.got_nameplates", {"1", "2", "3"}),
        ])
        l.rx_nameplates({"1", "2", "3", "4"})
        self.assertEqual(events, [
            ("rc.tx_list", ),
            ("rc.tx_list", ),
            ("i.got_nameplates", {"1", "2", "3"}),
            ("i.got_nameplates", {"1", "2", "3", "4"}),
        ])

    def test_reconnect(self):
        l, rc, i, events = self.build()
        l.refresh()
        l.connected()
        self.assertEqual(events, [
            ("rc.tx_list", ),
        ])
        events[:] = []
        l.lost()
        l.connected()
        self.assertEqual(events, [
            ("rc.tx_list", ),
        ])

    def test_refresh_first(self):
        l, rc, i, events = self.build()
        l.refresh()
        self.assertEqual(events, [])
        l.connected()
        self.assertEqual(events, [
            ("rc.tx_list", ),
        ])
        l.rx_nameplates({"1", "2", "3"})
        self.assertEqual(events, [
            ("rc.tx_list", ),
            ("i.got_nameplates", {"1", "2", "3"}),
        ])

    def test_unrefreshed(self):
        l, rc, i, events = self.build()
        self.assertEqual(events, [])
        # we receive a spontaneous rx_nameplates, without asking
        l.connected()
        self.assertEqual(events, [])
        l.rx_nameplates({"1", "2", "3"})
        self.assertEqual(events, [
            ("i.got_nameplates", {"1", "2", "3"}),
        ])


class Allocator(unittest.TestCase):
    def build(self):
        events = []
        a = _allocator.Allocator(timing.DebugTiming())
        rc = Dummy("rc", events, IRendezvousConnector, "tx_allocate")
        c = Dummy("c", events, ICode, "allocated")
        a.wire(rc, c)
        return a, rc, c, events

    def test_no_allocation(self):
        a, rc, c, events = self.build()
        a.connected()
        self.assertEqual(events, [])

    def test_allocate_first(self):
        a, rc, c, events = self.build()
        a.allocate(2, FakeWordList())
        self.assertEqual(events, [])
        a.connected()
        self.assertEqual(events, [("rc.tx_allocate", )])
        events[:] = []
        a.lost()
        a.connected()
        self.assertEqual(events, [
            ("rc.tx_allocate", ),
        ])
        events[:] = []
        a.rx_allocated("1")
        self.assertEqual(events, [
            ("c.allocated", "1", "1-word-word"),
        ])

    def test_connect_first(self):
        a, rc, c, events = self.build()
        a.connected()
        self.assertEqual(events, [])
        a.allocate(2, FakeWordList())
        self.assertEqual(events, [("rc.tx_allocate", )])
        events[:] = []
        a.lost()
        a.connected()
        self.assertEqual(events, [
            ("rc.tx_allocate", ),
        ])
        events[:] = []
        a.rx_allocated("1")
        self.assertEqual(events, [
            ("c.allocated", "1", "1-word-word"),
        ])


class Nameplate(unittest.TestCase):
    def build(self):
        events = []
        n = _nameplate.Nameplate()
        m = Dummy("m", events, IMailbox, "got_mailbox")
        i = Dummy("i", events, IInput, "got_wordlist")
        rc = Dummy("rc", events, IRendezvousConnector, "tx_claim",
                   "tx_release")
        t = Dummy("t", events, ITerminator, "nameplate_done")
        n.wire(m, i, rc, t)
        return n, m, i, rc, t, events

    def test_set_invalid(self):
        n, m, i, rc, t, events = self.build()
        with self.assertRaises(errors.KeyFormatError) as e:
            n.set_nameplate(" 1")
        self.assertEqual(
            str(e.exception),
            "Nameplate ' 1' must be numeric, with no spaces.")
        with self.assertRaises(errors.KeyFormatError) as e:
            n.set_nameplate("one")
        self.assertEqual(
            str(e.exception),
            "Nameplate 'one' must be numeric, with no spaces.")

        # wormhole should still be usable
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])

    def test_set_first(self):
        # connection remains up throughout
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_connect_first(self):
        # connection remains up throughout
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_reconnect_while_claiming(self):
        # connection bounced while waiting for rx_claimed
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        n.lost()
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])

    def test_reconnect_while_claimed(self):
        # connection bounced while claimed: no retransmits should be sent
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.lost()
        n.connected()
        self.assertEqual(events, [])

    def test_reconnect_while_releasing(self):
        # connection bounced while waiting for rx_released
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.lost()
        n.connected()
        self.assertEqual(events, [("rc.tx_release", "1")])

    def test_reconnect_while_done(self):
        # connection bounces after we're done
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])
        events[:] = []

        n.lost()
        n.connected()
        self.assertEqual(events, [])

    def test_close_while_idle(self):
        n, m, i, rc, t, events = self.build()
        n.close()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_idle_connected(self):
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])
        n.close()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_unclaimed(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        n.close()  # before ever being connected
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_claiming(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        n.close()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_claiming_but_disconnected(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        n.lost()
        n.close()
        self.assertEqual(events, [])
        # we're now waiting for a connection, so we can release the nameplate
        n.connected()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_claimed(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.close()
        # this path behaves just like a deliberate release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_claimed_but_disconnected(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.lost()
        n.close()
        # we're now waiting for a connection, so we can release the nameplate
        n.connected()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_releasing(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.close()  # ignored, we're already on our way out the door
        self.assertEqual(events, [])
        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_releasing_but_disconnecteda(self):
        n, m, i, rc, t, events = self.build()
        n.set_nameplate("1")
        self.assertEqual(events, [])
        n.connected()
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.lost()
        n.close()
        # we must retransmit the tx_release when we reconnect
        self.assertEqual(events, [])

        n.connected()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])

    def test_close_while_done(self):
        # connection remains up throughout
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])
        events[:] = []

        n.close()  # NOP
        self.assertEqual(events, [])

    def test_close_while_done_but_disconnected(self):
        # connection remains up throughout
        n, m, i, rc, t, events = self.build()
        n.connected()
        self.assertEqual(events, [])

        n.set_nameplate("1")
        self.assertEqual(events, [("rc.tx_claim", "1")])
        events[:] = []

        wl = object()
        with mock.patch("wormhole._nameplate.PGPWordList", return_value=wl):
            n.rx_claimed("mbox1")
        self.assertEqual(events, [
            ("i.got_wordlist", wl),
            ("m.got_mailbox", "mbox1"),
        ])
        events[:] = []

        n.release()
        self.assertEqual(events, [("rc.tx_release", "1")])
        events[:] = []

        n.rx_released()
        self.assertEqual(events, [("t.nameplate_done", )])
        events[:] = []

        n.lost()
        n.close()  # NOP
        self.assertEqual(events, [])


class Mailbox(unittest.TestCase):
    def build(self):
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

    def assert_events(self, events, initial_events, tx_add_events):
        self.assertEqual(
            len(events),
            len(initial_events) + len(tx_add_events), events)
        self.assertEqual(events[:len(initial_events)], initial_events)
        self.assertEqual(set(events[len(initial_events):]), tx_add_events)

    def test_connect_first(self):  # connect before got_mailbox
        m, n, rc, o, t, events = self.build()
        m.add_message("phase1", b"msg1")
        self.assertEqual(events, [])

        m.connected()
        self.assertEqual(events, [])

        m.got_mailbox("mbox1")
        self.assertEqual(events, [("rc.tx_open", "mbox1"),
                                  ("rc.tx_add", "phase1", b"msg1")])
        events[:] = []

        m.add_message("phase2", b"msg2")
        self.assertEqual(events, [("rc.tx_add", "phase2", b"msg2")])
        events[:] = []

        # bouncing the connection should retransmit everything, even the open()
        m.lost()
        self.assertEqual(events, [])
        # and messages sent while here should be queued
        m.add_message("phase3", b"msg3")
        self.assertEqual(events, [])

        m.connected()
        # the other messages are allowed to be sent in any order
        self.assert_events(
            events, [("rc.tx_open", "mbox1")], {
                ("rc.tx_add", "phase1", b"msg1"),
                ("rc.tx_add", "phase2", b"msg2"),
                ("rc.tx_add", "phase3", b"msg3"),
            })
        events[:] = []

        m.rx_message("side1", "phase1",
                     b"msg1")  # echo of our message, dequeue
        self.assertEqual(events, [])

        m.lost()
        m.connected()
        self.assert_events(events, [("rc.tx_open", "mbox1")], {
            ("rc.tx_add", "phase2", b"msg2"),
            ("rc.tx_add", "phase3", b"msg3"),
        })
        events[:] = []

        # a new message from the peer gets delivered, and the Nameplate is
        # released since the message proves that our peer opened the Mailbox
        # and therefore no longer needs the Nameplate
        m.rx_message("side2", "phase1", b"msg1them")  # new message from peer
        self.assertEqual(events, [
            ("n.release", ),
            ("o.got_message", "side2", "phase1", b"msg1them"),
        ])
        events[:] = []

        # we de-duplicate peer messages, but still re-release the nameplate
        # since Nameplate is smart enough to ignore that
        m.rx_message("side2", "phase1", b"msg1them")
        self.assertEqual(events, [
            ("n.release", ),
        ])
        events[:] = []

        m.close("happy")
        self.assertEqual(events, [("rc.tx_close", "mbox1", "happy")])
        events[:] = []

        # while closing, we ignore a lot
        m.add_message("phase-late", b"late")
        m.rx_message("side1", "phase2", b"msg2")
        m.close("happy")
        self.assertEqual(events, [])

        # bouncing the connection forces a retransmit of the tx_close
        m.lost()
        self.assertEqual(events, [])
        m.connected()
        self.assertEqual(events, [("rc.tx_close", "mbox1", "happy")])
        events[:] = []

        m.rx_closed()
        self.assertEqual(events, [("t.mailbox_done", )])
        events[:] = []

        # while closed, we ignore everything
        m.add_message("phase-late", b"late")
        m.rx_message("side1", "phase2", b"msg2")
        m.close("happy")
        m.lost()
        m.connected()
        self.assertEqual(events, [])

    def test_mailbox_first(self):  # got_mailbox before connect
        m, n, rc, o, t, events = self.build()
        m.add_message("phase1", b"msg1")
        self.assertEqual(events, [])

        m.got_mailbox("mbox1")
        m.add_message("phase2", b"msg2")
        self.assertEqual(events, [])

        m.connected()

        self.assert_events(events, [("rc.tx_open", "mbox1")], {
            ("rc.tx_add", "phase1", b"msg1"),
            ("rc.tx_add", "phase2", b"msg2"),
        })

    def test_close_while_idle(self):
        m, n, rc, o, t, events = self.build()
        m.close("happy")
        self.assertEqual(events, [("t.mailbox_done", )])

    def test_close_while_idle_but_connected(self):
        m, n, rc, o, t, events = self.build()
        m.connected()
        m.close("happy")
        self.assertEqual(events, [("t.mailbox_done", )])

    def test_close_while_mailbox_disconnected(self):
        m, n, rc, o, t, events = self.build()
        m.got_mailbox("mbox1")
        m.close("happy")
        self.assertEqual(events, [("t.mailbox_done", )])

    def test_close_while_reconnecting(self):
        m, n, rc, o, t, events = self.build()
        m.got_mailbox("mbox1")
        m.connected()
        self.assertEqual(events, [("rc.tx_open", "mbox1")])
        events[:] = []

        m.lost()
        self.assertEqual(events, [])
        m.close("happy")
        self.assertEqual(events, [])
        # we now wait to connect, so we can send the tx_close

        m.connected()
        self.assertEqual(events, [("rc.tx_close", "mbox1", "happy")])
        events[:] = []

        m.rx_closed()
        self.assertEqual(events, [("t.mailbox_done", )])
        events[:] = []


class Terminator(unittest.TestCase):
    def build(self):
        events = []
        t = _terminator.Terminator()
        b = Dummy("b", events, IBoss, "closed")
        rc = Dummy("rc", events, IRendezvousConnector, "stop")
        n = Dummy("n", events, INameplate, "close")
        m = Dummy("m", events, IMailbox, "close")
        t.wire(b, rc, n, m)
        return t, b, rc, n, m, events

    # there are three events, and we need to test all orderings of them
    def _do_test(self, ev1, ev2, ev3):
        t, b, rc, n, m, events = self.build()
        input_events = {
            "mailbox": lambda: t.mailbox_done(),
            "nameplate": lambda: t.nameplate_done(),
            "close": lambda: t.close("happy"),
        }
        close_events = [
            ("n.close", ),
            ("m.close", "happy"),
        ]

        input_events[ev1]()
        expected = []
        if ev1 == "close":
            expected.extend(close_events)
        self.assertEqual(events, expected)
        events[:] = []

        input_events[ev2]()
        expected = []
        if ev2 == "close":
            expected.extend(close_events)
        self.assertEqual(events, expected)
        events[:] = []

        input_events[ev3]()
        expected = []
        if ev3 == "close":
            expected.extend(close_events)
        expected.append(("rc.stop", ))
        self.assertEqual(events, expected)
        events[:] = []

        t.stopped()
        self.assertEqual(events, [("b.closed", )])

    def test_terminate(self):
        self._do_test("mailbox", "nameplate", "close")
        self._do_test("mailbox", "close", "nameplate")
        self._do_test("nameplate", "mailbox", "close")
        self._do_test("nameplate", "close", "mailbox")
        self._do_test("close", "nameplate", "mailbox")
        self._do_test("close", "mailbox", "nameplate")

    # TODO: test moods


class MockBoss(_boss.Boss):
    def __attrs_post_init__(self):
        # self._build_workers()
        self._init_other_state()


class Boss(unittest.TestCase):
    def build(self):
        events = []
        wormhole = Dummy("w", events, None, "got_welcome", "got_code",
                         "got_key", "got_verifier", "got_versions", "received",
                         "closed")
        versions = {"app": "version1"}
        reactor = None
        journal = ImmediateJournal()
        tor_manager = None
        client_version = ("python", __version__)
        b = MockBoss(wormhole, "side", "url", "appid", versions,
                     client_version, reactor, journal, tor_manager,
                     timing.DebugTiming())
        b._T = Dummy("t", events, ITerminator, "close")
        b._S = Dummy("s", events, ISend, "send")
        b._RC = Dummy("rc", events, IRendezvousConnector, "start")
        b._C = Dummy("c", events, ICode, "allocate_code", "input_code",
                     "set_code")
        return b, events

    def test_basic(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.got_code("1-code")
        self.assertEqual(events, [("w.got_code", "1-code")])
        events[:] = []

        welcome = {"howdy": "how are ya"}
        b.rx_welcome(welcome)
        self.assertEqual(events, [
            ("w.got_welcome", welcome),
        ])
        events[:] = []

        # pretend a peer message was correctly decrypted
        b.got_key(b"key")
        b.happy()
        b.got_verifier(b"verifier")
        b.got_message("version", b"{}")
        b.got_message("0", b"msg1")
        self.assertEqual(events, [
            ("w.got_key", b"key"),
            ("w.got_verifier", b"verifier"),
            ("w.got_versions", {}),
            ("w.received", b"msg1"),
        ])
        events[:] = []

        b.send(b"msg2")
        self.assertEqual(events, [("s.send", "0", b"msg2")])
        events[:] = []

        b.close()
        self.assertEqual(events, [("t.close", "happy")])
        events[:] = []

        b.closed()
        self.assertEqual(events, [("w.closed", "happy")])

    def test_unwelcome(self):
        b, events = self.build()
        unwelcome = {"error": "go away"}
        b.rx_welcome(unwelcome)
        self.assertEqual(events, [("t.close", "unwelcome")])

    def test_lonely(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.got_code("1-code")
        self.assertEqual(events, [("w.got_code", "1-code")])
        events[:] = []

        b.close()
        self.assertEqual(events, [("t.close", "lonely")])
        events[:] = []

        b.closed()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], errors.LonelyError)

    def test_server_error(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        orig = {}
        b.rx_error("server-error-msg", orig)
        self.assertEqual(events, [("t.close", "errory")])
        events[:] = []

        b.closed()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], errors.ServerError)
        self.assertEqual(events[0][1].args[0], "server-error-msg")

    def test_internal_error(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.error(ValueError("catch me"))
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], ValueError)
        self.assertEqual(events[0][1].args[0], "catch me")

    def test_close_early(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.close()  # before even w.got_code
        self.assertEqual(events, [("t.close", "lonely")])
        events[:] = []

        b.closed()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], errors.LonelyError)

    def test_error_while_closing(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.close()
        self.assertEqual(events, [("t.close", "lonely")])
        events[:] = []

        b.error(ValueError("oops"))
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], ValueError)

    def test_scary_version(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.got_code("1-code")
        self.assertEqual(events, [("w.got_code", "1-code")])
        events[:] = []

        b.scared()
        self.assertEqual(events, [("t.close", "scary")])
        events[:] = []

        b.closed()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], errors.WrongPasswordError)

    def test_scary_phase(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.got_code("1-code")
        self.assertEqual(events, [("w.got_code", "1-code")])
        events[:] = []

        b.happy()  # phase=version

        b.scared()  # phase=0
        self.assertEqual(events, [("t.close", "scary")])
        events[:] = []

        b.closed()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "w.closed")
        self.assertIsInstance(events[0][1], errors.WrongPasswordError)

    def test_unknown_phase(self):
        b, events = self.build()
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])
        events[:] = []

        b.got_code("1-code")
        self.assertEqual(events, [("w.got_code", "1-code")])
        events[:] = []

        b.happy()  # phase=version

        b.got_message("unknown-phase", b"spooky")
        self.assertEqual(events, [])

        self.flushLoggedErrors(errors._UnknownPhaseError)

    def test_set_code_bad_format(self):
        b, events = self.build()
        with self.assertRaises(errors.KeyFormatError):
            b.set_code("1 code")
        # wormhole should still be usable
        b.set_code("1-code")
        self.assertEqual(events, [("c.set_code", "1-code")])

    def test_set_code_twice(self):
        b, events = self.build()
        b.set_code("1-code")
        with self.assertRaises(errors.OnlyOneCodeError):
            b.set_code("1-code")

    def test_input_code(self):
        b, events = self.build()
        b._C.retval = "helper"
        helper = b.input_code()
        self.assertEqual(events, [("c.input_code", )])
        self.assertEqual(helper, "helper")
        with self.assertRaises(errors.OnlyOneCodeError):
            b.input_code()

    def test_allocate_code(self):
        b, events = self.build()
        wl = object()
        with mock.patch("wormhole._boss.PGPWordList", return_value=wl):
            b.allocate_code(3)
        self.assertEqual(events, [("c.allocate_code", 3, wl)])
        with self.assertRaises(errors.OnlyOneCodeError):
            b.allocate_code(3)


class Rendezvous(unittest.TestCase):
    def build(self):
        events = []
        reactor = object()
        journal = ImmediateJournal()
        tor_manager = None
        client_version = ("python", __version__)
        rc = _rendezvous.RendezvousConnector(
            "ws://host:4000/v1", "appid", "side", reactor, journal,
            tor_manager, timing.DebugTiming(), client_version)
        b = Dummy("b", events, IBoss, "error")
        n = Dummy("n", events, INameplate, "connected", "lost")
        m = Dummy("m", events, IMailbox, "connected", "lost")
        a = Dummy("a", events, IAllocator, "connected", "lost")
        l = Dummy("l", events, ILister, "connected", "lost")
        t = Dummy("t", events, ITerminator)
        rc.wire(b, n, m, a, l, t)
        return rc, events

    def test_basic(self):
        rc, events = self.build()
        del rc, events

    def test_websocket_failure(self):
        # if the TCP connection succeeds, but the subsequent WebSocket
        # negotiation fails, then we'll see an onClose without first seeing
        # onOpen
        rc, events = self.build()
        rc.ws_close(False, 1006, "connection was closed uncleanly")
        # this should cause the ClientService to be shut down, and an error
        # delivered to the Boss
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0][0], "b.error")
        self.assertIsInstance(events[0][1], errors.ServerConnectionError)
        self.assertEqual(str(events[0][1]), "connection was closed uncleanly")

    def test_websocket_lost(self):
        # if the TCP connection succeeds, and negotiation completes, then the
        # connection is lost, several machines should be notified
        rc, events = self.build()

        ws = mock.Mock()

        def notrandom(length):
            return b"\x00" * length

        with mock.patch("os.urandom", notrandom):
            rc.ws_open(ws)
        self.assertEqual(events, [
            ("n.connected", ),
            ("m.connected", ),
            ("l.connected", ),
            ("a.connected", ),
        ])
        events[:] = []

        def sent_messages(ws):
            for c in ws.mock_calls:
                self.assertEqual(c[0], "sendMessage", ws.mock_calls)
                self.assertEqual(c[1][1], False, ws.mock_calls)
                yield bytes_to_dict(c[1][0])

        self.assertEqual(
            list(sent_messages(ws)), [
                dict(
                    appid="appid",
                    side="side",
                    client_version=["python", __version__],
                    id="0000",
                    type="bind"),
            ])

        rc.ws_close(True, None, None)
        self.assertEqual(events, [
            ("n.lost", ),
            ("m.lost", ),
            ("l.lost", ),
            ("a.lost", ),
        ])


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
