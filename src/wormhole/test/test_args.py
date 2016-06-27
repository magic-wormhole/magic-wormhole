import mock
from twisted.trial import unittest
from ..cli.cli import wormhole
from ..cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from click.testing import CliRunner
#from pprint import pprint

def run(argv):
    r = CliRunner()
    with mock.patch("wormhole.cli.cli.react") as react:
        r.invoke(wormhole, argv)
        cfg = react.call_args[0][1][0]
    return cfg

class Send(unittest.TestCase):
    def test_baseline(self):
        cfg = run(["send", "--text", "hi"])
        #pprint(cfg.__dict__)
        self.assertEqual(cfg.what, None)
        self.assertEqual(cfg.code, None)
        self.assertEqual(cfg.code_length, 2)
        self.assertEqual(cfg.dump_timing, None)
        self.assertEqual(cfg.hide_progress, False)
        self.assertEqual(cfg.listen, True)
        self.assertEqual(cfg.output_file, None)
        self.assertEqual(cfg.relay_url, RENDEZVOUS_RELAY)
        self.assertEqual(cfg.transit_helper, TRANSIT_RELAY)
        self.assertEqual(cfg.text, "hi")
        self.assertEqual(cfg.tor, False)
        self.assertEqual(cfg.verify, False)
        self.assertEqual(cfg.zeromode, False)

    def test_file(self):
        cfg = run(["send", "fn"])
        #pprint(cfg.__dict__)
        self.assertEqual(cfg.what, u"fn")
        self.assertEqual(cfg.text, None)

    def test_text(self):
        cfg = run(["send", "--text", "hi"])
        self.assertEqual(cfg.what, None)
        self.assertEqual(cfg.text, u"hi")

    def test_nolisten(self):
        cfg = run(["--no-listen", "send", "fn"])
        self.assertEqual(cfg.listen, False)

    def test_code(self):
        cfg = run(["send", "--code", "1-abc", "fn"])
        self.assertEqual(cfg.code, u"1-abc")

    def test_code_length(self):
        cfg = run(["-c", "3", "send", "fn"])
        self.assertEqual(cfg.code_length, 3)

    def test_dump_timing(self):
        cfg = run(["--dump-timing", "tx.json", "send", "fn"])
        self.assertEqual(cfg.dump_timing, "tx.json")

    def test_hide_progress(self):
        cfg = run(["--hide-progress", "send", "fn"])
        self.assertEqual(cfg.hide_progress, True)

    def test_tor(self):
        cfg = run(["--tor", "send", "fn"])
        self.assertEqual(cfg.tor, True)

    def test_verify(self):
        cfg = run(["--verify", "send", "fn"])
        self.assertEqual(cfg.verify, True)

    def test_zeromode(self):
        cfg = run(["send", "-0", "fn"])
        self.assertEqual(cfg.zeromode, True)

class Receive(unittest.TestCase):
    def test_baseline(self):
        cfg = run(["receive"])
        #pprint(cfg.__dict__)
        self.assertEqual(cfg.accept_file, False)
        self.assertEqual(cfg.what, None)
        self.assertEqual(cfg.code, None)
        self.assertEqual(cfg.code_length, 2)
        self.assertEqual(cfg.dump_timing, None)
        self.assertEqual(cfg.hide_progress, False)
        self.assertEqual(cfg.listen, True)
        self.assertEqual(cfg.only_text, False)
        self.assertEqual(cfg.output_file, None)
        self.assertEqual(cfg.relay_url, RENDEZVOUS_RELAY)
        self.assertEqual(cfg.transit_helper, TRANSIT_RELAY)
        self.assertEqual(cfg.text, None)
        self.assertEqual(cfg.tor, False)
        self.assertEqual(cfg.verify, False)
        self.assertEqual(cfg.zeromode, False)

    def test_nolisten(self):
        cfg = run(["--no-listen", "receive"])
        self.assertEqual(cfg.listen, False)

    def test_code(self):
        cfg = run(["receive", "1-abc"])
        self.assertEqual(cfg.code, u"1-abc")

    def test_code_length(self):
        cfg = run(["-c", "3", "receive"])
        self.assertEqual(cfg.code_length, 3)

    def test_dump_timing(self):
        cfg = run(["--dump-timing", "tx.json", "receive"])
        self.assertEqual(cfg.dump_timing, "tx.json")

    def test_hide_progress(self):
        cfg = run(["--hide-progress", "receive"])
        self.assertEqual(cfg.hide_progress, True)

    def test_tor(self):
        cfg = run(["--tor", "receive"])
        self.assertEqual(cfg.tor, True)

    def test_verify(self):
        cfg = run(["--verify", "receive"])
        self.assertEqual(cfg.verify, True)

    def test_zeromode(self):
        cfg = run(["receive", "-0"])
        self.assertEqual(cfg.zeromode, True)

    def test_only_text(self):
        cfg = run(["receive", "-t"])
        self.assertEqual(cfg.only_text, True)

    def test_accept_file(self):
        cfg = run(["receive", "--accept-file"])
        self.assertEqual(cfg.accept_file, True)

    def test_output_file(self):
        cfg = run(["receive", "--output-file", "fn"])
        self.assertEqual(cfg.output_file, u"fn")
