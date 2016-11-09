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

    def test_size_fmt_decimal(self):
        """test the size formatting routines"""
        si_size_map = {
            0: '0 B',  # no rounding necessary for those
            1: '1 B',
            142: '142 B',
            999: '999 B',
            1000: '1.00 kB',  # rounding starts here
            1001: '1.00 kB',  # should be rounded away
            1234: '1.23 kB',  # should be rounded down
            1235: '1.24 kB',  # should be rounded up
            1010: '1.01 kB',  # rounded down as well
            999990000: '999.99 MB',  # rounded down
            999990001: '999.99 MB',  # rounded down
            999995000: '1.00 GB',  # rounded up to next unit
            10**6: '1.00 MB',  # and all the remaining units, megabytes
            10**9: '1.00 GB',  # gigabytes
            10**12: '1.00 TB',  # terabytes
            10**15: '1.00 PB',  # petabytes
            10**18: '1.00 EB',  # exabytes
            10**21: '1.00 ZB',  # zottabytes
            10**24: '1.00 YB',  # yottabytes
            -1: '-1 B',  # negative value
            -1010: '-1.01 kB',  # negative value with rounding
        }
        for size, fmt in si_size_map.items():
            self.assertEqual(util.sizeof_fmt_decimal(size), fmt)
