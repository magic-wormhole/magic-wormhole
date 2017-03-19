from __future__ import print_function, unicode_literals
import mock
from twisted.trial import unittest
from .._wordlist import PGPWordList

class Completions(unittest.TestCase):
    def test_completions(self):
        wl = PGPWordList()
        gc = wl.get_completions
        self.assertEqual(gc("ar"), {"mistice", "ticle"})
        self.assertEqual(gc("armis"), {"tice"})
        self.assertEqual(gc("armistice-ba"),
                         {"boon", "ckfield", "ckward", "njo"})
        self.assertEqual(gc("armistice-baboon"), {""})

class Choose(unittest.TestCase):
    def test_choose_words(self):
        wl = PGPWordList()
        with mock.patch("os.urandom", side_effect=[b"\x04", b"\x10"]):
            self.assertEqual(wl.choose_words(2), "alkali-assume")
