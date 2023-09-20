import os
import sys

from twisted.trial import unittest

from unittest import mock

from ..cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from .common import config


class Send(unittest.TestCase):
    def test_baseline(self):
        cfg = config("send", "--text", "hi")
        self.assertEqual(cfg.what, None)
        self.assertEqual(cfg.code, None)
        self.assertEqual(cfg.code_length, 2)
        self.assertEqual(cfg.dump_timing, None)
        self.assertEqual(cfg.hide_progress, False)
        self.assertEqual(cfg.listen, True)
        self.assertEqual(cfg.appid, None)
        self.assertEqual(cfg.relay_url, RENDEZVOUS_RELAY)
        self.assertEqual(cfg.transit_helper, TRANSIT_RELAY)
        self.assertEqual(cfg.text, "hi")
        self.assertEqual(cfg.tor, False)
        self.assertEqual(cfg.verify, False)
        self.assertEqual(cfg.zeromode, False)

    def test_appid(self):
        cfg = config("--appid", "xyz", "send", "--text", "hi")
        self.assertEqual(cfg.appid, "xyz")
        cfg = config("--appid=xyz", "send", "--text", "hi")
        self.assertEqual(cfg.appid, "xyz")

    def test_file(self):
        cfg = config("send", "fn")
        self.assertEqual(cfg.what, u"fn")
        self.assertEqual(cfg.text, None)

    def test_text(self):
        cfg = config("send", "--text", "hi")
        self.assertEqual(cfg.what, None)
        self.assertEqual(cfg.text, u"hi")

    def test_nolisten(self):
        cfg = config("send", "--no-listen", "fn")
        self.assertEqual(cfg.listen, False)

    def test_code(self):
        cfg = config("send", "--code", "1-abc", "fn")
        self.assertEqual(cfg.code, u"1-abc")

    def test_code_length(self):
        cfg = config("send", "-c", "3", "fn")
        self.assertEqual(cfg.code_length, 3)

    def test_dump_timing(self):
        cfg = config("--dump-timing", "tx.json", "send", "fn")
        self.assertEqual(cfg.dump_timing, "tx.json")

    def test_hide_progress(self):
        cfg = config("send", "--hide-progress", "fn")
        self.assertEqual(cfg.hide_progress, True)

    def test_tor(self):
        cfg = config("send", "--tor", "fn")
        self.assertEqual(cfg.tor, True)

    def test_verify(self):
        cfg = config("send", "--verify", "fn")
        self.assertEqual(cfg.verify, True)

    def test_zeromode(self):
        cfg = config("send", "-0", "fn")
        self.assertEqual(cfg.zeromode, True)

    def test_relay_env_var(self):
        relay_url = str(mock.sentinel.relay_url)
        with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
            cfg = config("send")
        self.assertEqual(cfg.relay_url, relay_url)

        # Make sure cmd line option overrides environment variable
        relay_url_2 = str(mock.sentinel.relay_url_2)
        with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
            cfg = config("--relay-url", relay_url_2, "send")
        self.assertEqual(cfg.relay_url, relay_url_2)

    def test_transit_env_var(self):
        transit_url = str(mock.sentinel.transit_url)
        with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
            cfg = config("send")
        self.assertEqual(cfg.transit_helper, transit_url)

        # Make sure cmd line option overrides environment variable
        transit_url_2 = str(mock.sentinel.transit_url_2)
        with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
            cfg = config("--transit-helper", transit_url_2, "send")
        self.assertEqual(cfg.transit_helper, transit_url_2)


class Receive(unittest.TestCase):
    def test_baseline(self):
        cfg = config("receive")
        self.assertEqual(cfg.accept_file, False)
        self.assertEqual(cfg.code, None)
        self.assertEqual(cfg.code_length, 2)
        self.assertEqual(cfg.dump_timing, None)
        self.assertEqual(cfg.hide_progress, False)
        self.assertEqual(cfg.listen, True)
        self.assertEqual(cfg.only_text, False)
        self.assertEqual(cfg.output_file, None)
        self.assertEqual(cfg.appid, None)
        self.assertEqual(cfg.relay_url, RENDEZVOUS_RELAY)
        self.assertEqual(cfg.transit_helper, TRANSIT_RELAY)
        self.assertEqual(cfg.tor, False)
        self.assertEqual(cfg.verify, False)
        self.assertEqual(cfg.zeromode, False)

    def test_appid(self):
        cfg = config("--appid", "xyz", "receive")
        self.assertEqual(cfg.appid, "xyz")
        cfg = config("--appid=xyz", "receive")
        self.assertEqual(cfg.appid, "xyz")

    def test_nolisten(self):
        cfg = config("receive", "--no-listen")
        self.assertEqual(cfg.listen, False)

    def test_code(self):
        cfg = config("receive", "1-abc")
        self.assertEqual(cfg.code, u"1-abc")

    def test_code_length(self):
        cfg = config("receive", "-c", "3", "--allocate")
        self.assertEqual(cfg.code_length, 3)

    def test_dump_timing(self):
        cfg = config("--dump-timing", "tx.json", "receive")
        self.assertEqual(cfg.dump_timing, "tx.json")

    def test_hide_progress(self):
        cfg = config("receive", "--hide-progress")
        self.assertEqual(cfg.hide_progress, True)

    def test_tor(self):
        cfg = config("receive", "--tor")
        self.assertEqual(cfg.tor, True)

    def test_verify(self):
        cfg = config("receive", "--verify")
        self.assertEqual(cfg.verify, True)

    def test_zeromode(self):
        cfg = config("receive", "-0")
        self.assertEqual(cfg.zeromode, True)

    def test_only_text(self):
        cfg = config("receive", "-t")
        self.assertEqual(cfg.only_text, True)

    def test_accept_file(self):
        cfg = config("receive", "--accept-file")
        self.assertEqual(cfg.accept_file, True)

    def test_output_file(self):
        cfg = config("receive", "--output-file", "fn")
        self.assertEqual(cfg.output_file, u"fn")

    def test_relay_env_var(self):
        relay_url = str(mock.sentinel.relay_url)
        with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
            cfg = config("receive")
        self.assertEqual(cfg.relay_url, relay_url)

        # Make sure cmd line option overrides environment variable
        relay_url_2 = str(mock.sentinel.relay_url_2)
        with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
            cfg = config("--relay-url", relay_url_2, "receive")
        self.assertEqual(cfg.relay_url, relay_url_2)

    def test_transit_env_var(self):
        transit_url = str(mock.sentinel.transit_url)
        with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
            cfg = config("receive")
        self.assertEqual(cfg.transit_helper, transit_url)

        # Make sure cmd line option overrides environment variable
        transit_url_2 = str(mock.sentinel.transit_url_2)
        with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
            cfg = config("--transit-helper", transit_url_2, "receive")
        self.assertEqual(cfg.transit_helper, transit_url_2)


class Config(unittest.TestCase):
    def test_send(self):
        cfg = config("send")
        self.assertEqual(cfg.stdout, sys.stdout)

    def test_receive(self):
        cfg = config("receive")
        self.assertEqual(cfg.stdout, sys.stdout)
