from __future__ import print_function, unicode_literals
import json
from zope.interface import directlyProvides
from twisted.trial import unittest
from .. import timing, _order, _receive, _key
from .._interfaces import IKey, IReceive, IBoss, ISend, IMailbox
from .._key import derive_key, derive_phase_key, encrypt_data
from ..util import dict_to_bytes, hexstr_to_bytes, bytes_to_hexstr, to_bytes
from spake2 import SPAKE2_Symmetric

class Dummy:
    def __init__(self, name, events, iface, *meths):
        self.name = name
        self.events = events
        directlyProvides(self, iface)
        for meth in meths:
            self.mock(meth)
    def mock(self, meth):
        def log(*args):
            self.events.append(("%s.%s" % (self.name, meth),) + args)
        setattr(self, meth, log)

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
        self.assertEqual(events, [("k.got_pake", b"body")]) # right away
        o.got_message(u"side", u"version", b"body")
        o.got_message(u"side", u"1", b"body")
        self.assertEqual(events,
                         [("k.got_pake", b"body"),
                          ("r.got_message", u"side", u"version", b"body"),
                          ("r.got_message", u"side", u"1", b"body"),
                          ])

    def test_out_of_order(self):
        o, k, r, events = self.build()
        o.got_message(u"side", u"version", b"body")
        self.assertEqual(events, []) # nothing yet
        o.got_message(u"side", u"1", b"body")
        self.assertEqual(events, []) # nothing yet
        o.got_message(u"side", u"pake", b"body")
        # got_pake is delivered first
        self.assertEqual(events,
                         [("k.got_pake", b"body"),
                          ("r.got_message", u"side", u"version", b"body"),
                          ("r.got_message", u"side", u"1", b"body"),
                          ])

class Receive(unittest.TestCase):
    def build(self):
        events = []
        r = _receive.Receive(u"side", timing.DebugTiming())
        b = Dummy("b", events, IBoss, "happy", "scared", "got_message")
        s = Dummy("s", events, ISend, "got_verified_key")
        r.wire(b, s)
        return r, b, s, events

    def test_good(self):
        r, b, s, events = self.build()
        key = b"key"
        r.got_key(key)
        self.assertEqual(events, [])
        phase1_key = derive_phase_key(key, u"side", u"phase1")
        data1 = b"data1"
        good_body = encrypt_data(phase1_key, data1)
        r.got_message(u"side", u"phase1", good_body)
        self.assertEqual(events, [("s.got_verified_key", key),
                                  ("b.happy",),
                                  ("b.got_message", u"phase1", data1),
                                  ])

        phase2_key = derive_phase_key(key, u"side", u"phase2")
        data2 = b"data2"
        good_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", good_body)
        self.assertEqual(events, [("s.got_verified_key", key),
                                  ("b.happy",),
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
        self.assertEqual(events, [("b.scared",),
                                  ])

        phase2_key = derive_phase_key(key, u"side", u"phase2")
        data2 = b"data2"
        good_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", good_body)
        self.assertEqual(events, [("b.scared",),
                                  ])

    def test_late_bad(self):
        r, b, s, events = self.build()
        key = b"key"
        r.got_key(key)
        self.assertEqual(events, [])
        phase1_key = derive_phase_key(key, u"side", u"phase1")
        data1 = b"data1"
        good_body = encrypt_data(phase1_key, data1)
        r.got_message(u"side", u"phase1", good_body)
        self.assertEqual(events, [("s.got_verified_key", key),
                                  ("b.happy",),
                                  ("b.got_message", u"phase1", data1),
                                  ])

        phase2_key = derive_phase_key(key, u"side", u"bad")
        data2 = b"data2"
        bad_body = encrypt_data(phase2_key, data2)
        r.got_message(u"side", u"phase2", bad_body)
        self.assertEqual(events, [("s.got_verified_key", key),
                                  ("b.happy",),
                                  ("b.got_message", u"phase1", data1),
                                  ("b.scared",),
                                  ])
        r.got_message(u"side", u"phase1", good_body)
        r.got_message(u"side", u"phase2", bad_body)
        self.assertEqual(events, [("s.got_verified_key", key),
                                  ("b.happy",),
                                  ("b.got_message", u"phase1", data1),
                                  ("b.scared",),
                                  ])

class Key(unittest.TestCase):
    def test_derive_errors(self):
        self.assertRaises(TypeError, derive_key, 123, b"purpose")
        self.assertRaises(TypeError, derive_key, b"key", 123)
        self.assertRaises(TypeError, derive_key, b"key", b"purpose", "not len")

    def build(self):
        events = []
        k = _key.Key(u"appid", u"side", timing.DebugTiming())
        b = Dummy("b", events, IBoss, "scared", "got_key", "got_verifier")
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
        msg1_json = events[0][2]
        events[:] = []
        msg1 = json.loads(msg1_json)
        msg1_bytes = hexstr_to_bytes(msg1["pake_v1"])
        sp = SPAKE2_Symmetric(to_bytes(code), idSymmetric=to_bytes(u"appid"))
        msg2_bytes = sp.start()
        key2 = sp.finish(msg1_bytes)
        msg2 = dict_to_bytes({"pake_v1": bytes_to_hexstr(msg2_bytes)})
        k.got_pake(msg2)
        self.assertEqual(len(events), 4, events)
        self.assertEqual(events[0], ("b.got_key", key2))
        self.assertEqual(events[1][0], "b.got_verifier")
        self.assertEqual(events[2][:2], ("m.add_message", "version"))
        self.assertEqual(events[3], ("r.got_key", key2))


    def test_bad(self):
        k, b, m, r, events = self.build()
        code = u"1-foo"
        k.got_code(code)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][:2], ("m.add_message", "pake"))
        pake_1_json = events[0][2]
        pake_1 = json.loads(pake_1_json)
        self.assertEqual(pake_1.keys(), ["pake_v1"]) # value is PAKE stuff
        events[:] = []
        bad_pake_d = {"not_pake_v1": "stuff"}
        k.got_pake(dict_to_bytes(bad_pake_d))
        self.assertEqual(events, [("b.scared",)])
