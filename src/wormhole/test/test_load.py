import unittest

class Import(unittest.TestCase):
    def test_import(self):
        import wormhole
        self.assertTrue(len(wormhole.__version__))
        import wormhole_server
        self.assertTrue(len(wormhole_server.__version__))
        import txwormhole
        self.assertTrue(len(txwormhole.__version__))
        import wormhole_cli
        self.assertTrue(len(wormhole_cli.__version__))
