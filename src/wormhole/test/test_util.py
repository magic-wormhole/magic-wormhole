from __future__ import unicode_literals
import unicodedata
from twisted.trial import unittest
from .. import util

class Utils(unittest.TestCase):
    def test_to_bytes(self):
        b = util.to_bytes("abc")
        self.assertIsInstance(b, type(b""))
        self.assertEqual(b, b"abc")

        A = unicodedata.lookup("LATIN SMALL LETTER A WITH DIAERESIS")
        b = util.to_bytes(A + "bc")
        self.assertIsInstance(b, type(b""))
        self.assertEqual(b, b"\xc3\xa4\x62\x63")

    def test_bytes_to_hexstr(self):
        b = b"\x00\x45\x91\xfe\xff"
        hexstr = util.bytes_to_hexstr(b)
        self.assertIsInstance(hexstr, type(""))
        self.assertEqual(hexstr, "004591feff")

    def test_hexstr_to_bytes(self):
        hexstr = "004591feff"
        b = util.hexstr_to_bytes(hexstr)
        hexstr = util.bytes_to_hexstr(b)
        self.assertIsInstance(b, type(b""))
        self.assertEqual(b, b"\x00\x45\x91\xfe\xff")

    def test_dict_to_bytes(self):
        d = {"a": "b"}
        b = util.dict_to_bytes(d)
        self.assertIsInstance(b, type(b""))
        self.assertEqual(b, b'{"a": "b"}')

    def test_bytes_to_dict(self):
        b = b'{"a": "b", "c": 2}'
        d = util.bytes_to_dict(b)
        self.assertIsInstance(d, dict)
        self.assertEqual(d, {"a": "b", "c": 2})

class Space(unittest.TestCase):
    def test_free_space(self):
        free = util.estimate_free_space(".")
        self.assert_(isinstance(free, (int, type(None))), repr(free))
        # some platforms (I think the VMs used by travis are in this
        # category) return 0, and windows will return None, so don't assert
        # anything more specific about the return value
