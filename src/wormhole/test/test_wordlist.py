from __future__ import print_function, unicode_literals

from twisted.trial import unittest

import mock

from .._wordlist import PGPWordList


class Completions(unittest.TestCase):
    def test_completions(self):
        wl = PGPWordList()
        gc = wl.get_completions
        self.assertEqual(gc("ar", 2), {"armistice-", "article-"})
        self.assertEqual(gc("armis", 2), {"armistice-"})
        self.assertEqual(gc("armistice", 2), {"armistice-"})
        lots = gc("armistice-", 2)
        self.assertEqual(len(lots), 256, lots)
        first = list(lots)[0]
        self.assert_(first.startswith("armistice-"), first)
        self.assertEqual(
            gc("armistice-ba", 2), {
                "armistice-baboon", "armistice-backfield",
                "armistice-backward", "armistice-banjo"
            })
        self.assertEqual(
            gc("armistice-ba", 3), {
                "armistice-baboon-", "armistice-backfield-",
                "armistice-backward-", "armistice-banjo-"
            })
        self.assertEqual(gc("armistice-baboon", 2), {"armistice-baboon"})
        self.assertEqual(gc("armistice-baboon", 3), {"armistice-baboon-"})
        self.assertEqual(gc("armistice-baboon", 4), {"armistice-baboon-"})


class Choose(unittest.TestCase):
    def test_choose_words(self):
        wl = PGPWordList()
        with mock.patch("os.urandom", side_effect=[b"\x04", b"\x10"]):
            self.assertEqual(wl.choose_words(2), "alkali-assume")
