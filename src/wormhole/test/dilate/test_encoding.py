from twisted.trial import unittest
from ..._dilation.encode import to_be4, from_be4


class Encoding(unittest.TestCase):

    def test_be4(self):
        self.assertEqual(to_be4(0), b"\x00\x00\x00\x00")
        self.assertEqual(to_be4(1), b"\x00\x00\x00\x01")
        self.assertEqual(to_be4(256), b"\x00\x00\x01\x00")
        self.assertEqual(to_be4(257), b"\x00\x00\x01\x01")
        with self.assertRaises(ValueError):
            to_be4(-1)
        with self.assertRaises(ValueError):
            to_be4(2**32)

        self.assertEqual(from_be4(b"\x00\x00\x00\x00"), 0)
        self.assertEqual(from_be4(b"\x00\x00\x00\x01"), 1)
        self.assertEqual(from_be4(b"\x00\x00\x01\x00"), 256)
        self.assertEqual(from_be4(b"\x00\x00\x01\x01"), 257)

        with self.assertRaises(TypeError):
            from_be4(0)
        with self.assertRaises(ValueError):
            from_be4(b"\x01\x00\x00\x00\x00")
