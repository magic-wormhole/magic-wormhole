from __future__ import print_function, unicode_literals
import mock
from twisted.trial import unittest
from .._wordlist import PGPWordList

class Completions(unittest.TestCase):
    def test_completions(self):
        wl = PGPWordList()
        gc = wl.get_completions
        self.assertEqual(gc("ar", 2), {"mistice-", "ticle-"})
        self.assertEqual(gc("armis", 2), {"tice-"})
        self.assertEqual(gc("armistice", 2), {"-"})
        self.assertEqual(gc("armistice-ba", 2),
                         {"boon", "ckfield", "ckward", "njo"})
        self.assertEqual(gc("armistice-ba", 3),
                         {"boon-", "ckfield-", "ckward-", "njo-"})
        self.assertEqual(gc("armistice-baboon", 2), {""})
        self.assertEqual(gc("armistice-baboon", 3), {"-"})
        self.assertEqual(gc("armistice-baboon", 4), {"-"})

class Choose(unittest.TestCase):
    def test_choose_words(self):
        wl = PGPWordList()
        with mock.patch("os.urandom", side_effect=[b"\x04", b"\x10"]):
            self.assertEqual(wl.choose_words(2), "alkali-assume")
