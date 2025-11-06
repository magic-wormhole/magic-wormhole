import os
import sys

from unittest import mock

from ..cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from .common import config

def test_send_baseline():
    cfg = config("send", "--text", "hi")
    assert cfg.what is None
    assert cfg.code is None
    assert cfg.code_length == 2
    assert cfg.dump_timing is None
    assert not cfg.hide_progress
    assert cfg.listen
    assert cfg.appid is None
    assert cfg.relay_url == RENDEZVOUS_RELAY
    assert cfg.transit_helper == TRANSIT_RELAY
    assert cfg.text == "hi"
    assert not cfg.tor
    assert not cfg.verify
    assert not cfg.zeromode

def test_send_appid():
    cfg = config("--appid", "xyz", "send", "--text", "hi")
    assert cfg.appid == "xyz"
    cfg = config("--appid=xyz", "send", "--text", "hi")
    assert cfg.appid == "xyz"

def test_send_file():
    cfg = config("send", "fn")
    assert cfg.what == "fn"
    assert cfg.text is None

def test_send_text():
    cfg = config("send", "--text", "hi")
    assert cfg.what is None
    assert cfg.text == "hi"

def test_send_nolisten():
    cfg = config("send", "--no-listen", "fn")
    assert not cfg.listen

def test_send_code():
    cfg = config("send", "--code", "1-abc", "fn")
    assert cfg.code == "1-abc"

def test_send_code_length():
    cfg = config("send", "-c", "3", "fn")
    assert cfg.code_length == 3

def test_send_dump_timing():
    cfg = config("--dump-timing", "tx.json", "send", "fn")
    assert cfg.dump_timing == "tx.json"

def test_send_hide_progress():
    cfg = config("send", "--hide-progress", "fn")
    assert cfg.hide_progress

def test_send_tor():
    cfg = config("send", "--tor", "fn")
    assert cfg.tor

def test_send_verify():
    cfg = config("send", "--verify", "fn")
    assert cfg.verify

def test_send_zeromode():
    cfg = config("send", "-0", "fn")
    assert cfg.zeromode

def test_send_relay_env_var():
    relay_url = str(mock.sentinel.relay_url)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("send")
    assert cfg.relay_url == relay_url

    # Make sure cmd line option overrides environment variable
    relay_url_2 = str(mock.sentinel.relay_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("--relay-url", relay_url_2, "send")
    assert cfg.relay_url == relay_url_2

def test_send_transit_env_var():
    transit_url = str(mock.sentinel.transit_url)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("send")
    assert cfg.transit_helper == transit_url

    # Make sure cmd line option overrides environment variable
    transit_url_2 = str(mock.sentinel.transit_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("--transit-helper", transit_url_2, "send")
    assert cfg.transit_helper == transit_url_2


def test_receive_baseline():
    cfg = config("receive")
    assert not cfg.accept_file
    assert cfg.code is None
    assert cfg.code_length == 2
    assert cfg.dump_timing is None
    assert not cfg.hide_progress
    assert cfg.listen
    assert not cfg.only_text
    assert cfg.output_file is None
    assert cfg.appid is None
    assert cfg.relay_url == RENDEZVOUS_RELAY
    assert cfg.transit_helper == TRANSIT_RELAY
    assert not cfg.tor
    assert not cfg.verify
    assert not cfg.zeromode

def test_receive_appid():
    cfg = config("--appid", "xyz", "receive")
    assert cfg.appid == "xyz"
    cfg = config("--appid=xyz", "receive")
    assert cfg.appid == "xyz"

def test_receive_nolisten():
    cfg = config("receive", "--no-listen")
    assert not cfg.listen

def test_receive_code():
    cfg = config("receive", "1-abc")
    assert cfg.code == "1-abc"

def test_receive_code_length():
    cfg = config("receive", "-c", "3", "--allocate")
    assert cfg.code_length == 3

def test_receive_dump_timing():
    cfg = config("--dump-timing", "tx.json", "receive")
    assert cfg.dump_timing == "tx.json"

def test_receive_hide_progress():
    cfg = config("receive", "--hide-progress")
    assert cfg.hide_progress

def test_receive_tor():
    cfg = config("receive", "--tor")
    assert cfg.tor

def test_receive_verify():
    cfg = config("receive", "--verify")
    assert cfg.verify

def test_receive_zeromode():
    cfg = config("receive", "-0")
    assert cfg.zeromode

def test_receive_only_text():
    cfg = config("receive", "-t")
    assert cfg.only_text

def test_receive_accept_file():
    cfg = config("receive", "--accept-file")
    assert cfg.accept_file

def test_receive_output_file():
    cfg = config("receive", "--output-file", "fn")
    assert cfg.output_file == "fn"

def test_receive_relay_env_var():
    relay_url = str(mock.sentinel.relay_url)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("receive")
    assert cfg.relay_url == relay_url

    # Make sure cmd line option overrides environment variable
    relay_url_2 = str(mock.sentinel.relay_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_RELAY_URL=relay_url):
        cfg = config("--relay-url", relay_url_2, "receive")
    assert cfg.relay_url == relay_url_2

def test_receive_transit_env_var():
    transit_url = str(mock.sentinel.transit_url)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("receive")
    assert cfg.transit_helper == transit_url

    # Make sure cmd line option overrides environment variable
    transit_url_2 = str(mock.sentinel.transit_url_2)
    with mock.patch.dict(os.environ, WORMHOLE_TRANSIT_HELPER=transit_url):
        cfg = config("--transit-helper", transit_url_2, "receive")
    assert cfg.transit_helper == transit_url_2

def test_receive_accept_file_env_var():
    with mock.patch.dict(os.environ, WORMHOLE_ACCEPT_FILE="true"):
        cfg = config("receive")
    assert cfg.accept_file


def test_receive_send():
    cfg = config("send")
    assert cfg.stdout == sys.stdout

def test_receive_receive():
    cfg = config("receive")
    assert cfg.stdout == sys.stdout
