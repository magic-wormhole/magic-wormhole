import unittest

class Import(unittest.TestCase):
    def test_import(self):
        import wormhole
        self.assertTrue(len(wormhole.__version__))
