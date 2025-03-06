from twisted.trial import unittest
from wormhole._dilation.manager import _find_shared_versions

class Version(unittest.TestCase):
    def test_versions(self):
        assert _find_shared_versions([],[]) == None
        self.assertEqual(_find_shared_versions(["foo"],["foo"]),"foo")
        self.assertEqual(_find_shared_versions(["foo","bar"],["foo"]),"foo")
        self.assertEqual(_find_shared_versions(["bar","foo"],["foo","bar"]),"bar")
        self.assertEqual(_find_shared_versions([],["foo","bar"]),None)
        self.assertEqual(_find_shared_versions(["foo","bar"],[]),None)
