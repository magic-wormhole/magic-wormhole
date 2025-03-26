import os
import sys

from unittest import mock

from ..cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from .common import config


def test_baseline():
    cfg = config("send", "--text", "hi")
    assert cfg.what == None
    assert cfg.code == None
    assert cfg.code_length == 2
    assert cfg.dump_timing == None
    assert cfg.hide_progress == False
    assert cfg.listen == True
    assert cfg.appid == None
    assert cfg.relay_url == RENDEZVOUS_RELAY
    assert cfg.transit_helper == TRANSIT_RELAY
    assert cfg.text == "hi"
    assert cfg.tor == False
    assert cfg.verify == False
    assert cfg.zeromode == False

def test_appid():
    cfg = config("--appid", "xyz", "send", "--text", "hi")
    assert cfg.appid == "xyz"
    cfg = config("--appid=xyz", "send", "--text", "hi")
    assert cfg.appid == "xyz"

def test_file():
    cfg = config("send", "fn")
    assert cfg.what == u"fn"
    assert cfg.text == None

def test_text():
    cfg = config("send", "--text", "hi")
    assert cfg.what == None
    assert cfg.text == u"hi"

def test_nolisten():
    cfg = config("send", "--no-listen", "fn")
    assert cfg.listen == False

def test_code():
    cfg = config("send", "--code", "1-abc", "fn")
    assert cfg.code == u"1-abc"

def test_code_length():
    cfg = config("send", "-c", "3", "fn")
    assert cfg.code_length == 3

def test_dump_timing():
    cfg = config("--dump-timing", "tx.json", "send", "fn")
    assert cfg.dump_timing == "tx.json"

def test_hide_progress():
    cfg = config("send", "--hide-progress", "fn")
    assert cfg.hide_progress == True

def test_tor():
    cfg = config("send", "--tor", "fn")
    assert cfg.tor == True

def test_verify():
    cfg = config("send", "--verify", "fn")
    assert cfg.verify == True

def test_zeromode():
    cfg = config("send", "-0", "fn")
    assert cfg.zeromode == True

def test_relay_env_var():
    relay_url = str(mock.sentinel.relay_url)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("send")
    assert cfg.relay_url == relay_url

    # Make sure cmd line option overrides environment variable
    relay_url_2 = str(mock.sentinel.relay_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("--relay-url", relay_url_2, "send")
    assert cfg.relay_url == relay_url_2

def test_transit_env_var():
    transit_url = str(mock.sentinel.transit_url)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("send")
    assert cfg.transit_helper == transit_url

    # Make sure cmd line option overrides environment variable
    transit_url_2 = str(mock.sentinel.transit_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("--transit-helper", transit_url_2, "send")
    assert cfg.transit_helper == transit_url_2


def test_baseline():
    cfg = config("receive")
    assert cfg.accept_file == False
    assert cfg.code == None
    assert cfg.code_length == 2
    assert cfg.dump_timing == None
    assert cfg.hide_progress == False
    assert cfg.listen == True
    assert cfg.only_text == False
    assert cfg.output_file == None
    assert cfg.appid == None
    assert cfg.relay_url == RENDEZVOUS_RELAY
    assert cfg.transit_helper == TRANSIT_RELAY
    assert cfg.tor == False
    assert cfg.verify == False
    assert cfg.zeromode == False

def test_appid():
    cfg = config("--appid", "xyz", "receive")
    assert cfg.appid == "xyz"
    cfg = config("--appid=xyz", "receive")
    assert cfg.appid == "xyz"

def test_nolisten():
    cfg = config("receive", "--no-listen")
    assert cfg.listen == False

def test_code():
    cfg = config("receive", "1-abc")
    assert cfg.code == u"1-abc"

def test_code_length():
    cfg = config("receive", "-c", "3", "--allocate")
    assert cfg.code_length == 3

def test_dump_timing():
    cfg = config("--dump-timing", "tx.json", "receive")
    assert cfg.dump_timing == "tx.json"

def test_hide_progress():
    cfg = config("receive", "--hide-progress")
    assert cfg.hide_progress == True

def test_tor():
    cfg = config("receive", "--tor")
    assert cfg.tor == True

def test_verify():
    cfg = config("receive", "--verify")
    assert cfg.verify == True

def test_zeromode():
    cfg = config("receive", "-0")
    assert cfg.zeromode == True

def test_only_text():
    cfg = config("receive", "-t")
    assert cfg.only_text == True

def test_accept_file():
    cfg = config("receive", "--accept-file")
    assert cfg.accept_file == True

def test_output_file():
    cfg = config("receive", "--output-file", "fn")
    assert cfg.output_file == u"fn"

def test_relay_env_var():
    relay_url = str(mock.sentinel.relay_url)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("receive")
    assert cfg.relay_url == relay_url

    # Make sure cmd line option overrides environment variable
    relay_url_2 = str(mock.sentinel.relay_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("--relay-url", relay_url_2, "receive")
    assert cfg.relay_url == relay_url_2

def test_transit_env_var():
    transit_url = str(mock.sentinel.transit_url)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("receive")
    assert cfg.transit_helper == transit_url

    # Make sure cmd line option overrides environment variable
    transit_url_2 = str(mock.sentinel.transit_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("--transit-helper", transit_url_2, "receive")
    assert cfg.transit_helper == transit_url_2

def test_accept_file_env_var():
    with mock.patch.dict(os.environ, WORMHOLE_ACCEPT_FILE="true"):
        cfg = config("receive")
    assert cfg.accept_file == True


def test_send():
    cfg = config("send")
    assert cfg.stdout == sys.stdout

def test_receive():
    cfg = config("receive")
    assert cfg.stdout == sys.stdout
