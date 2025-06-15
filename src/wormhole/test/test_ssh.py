import io
import os

from unittest import mock

from ..cli import cmd_ssh
import pytest

OTHERS = ["config", "config~", "known_hosts", "known_hosts~"]


def test_find_one():
    files = OTHERS + ["id_rsa.pub", "id_rsa"]
    pubkey_data = "ssh-rsa AAAAkeystuff email@host\n"
    pubkey_file = io.StringIO(pubkey_data)
    with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
        with mock.patch("os.listdir", return_value=files) as ld:
            with mock.patch(
                    "wormhole.cli.cmd_ssh.open", return_value=pubkey_file):
                res = cmd_ssh.find_public_key()
    assert ld.mock_calls == \
                     [mock.call(os.path.expanduser("~/.ssh/"))]
    assert len(res) == 3, res
    kind, keyid, pubkey = res
    assert kind == "ssh-rsa"
    assert keyid == "email@host"
    assert pubkey == pubkey_data

def test_find_none():
    files = OTHERS  # no pubkey
    with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
        with mock.patch("os.listdir", return_value=files):
            with pytest.raises(cmd_ssh.PubkeyError) as f:
                cmd_ssh.find_public_key()
    dot_ssh = os.path.expanduser("~/.ssh/")
    assert str(f.value) == f"No public keys in '{dot_ssh}'"

def test_bad_hint():
    with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=False):
        with pytest.raises(cmd_ssh.PubkeyError) as f:
            cmd_ssh.find_public_key(hint="bogus/path")
    assert str(f.value) == "Can't find 'bogus/path'"

def test_find_multiple():
    files = OTHERS + ["id_rsa.pub", "id_rsa", "id_dsa.pub", "id_dsa"]
    pubkey_data = "ssh-rsa AAAAkeystuff email@host\n"
    pubkey_file = io.StringIO(pubkey_data)
    with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
        with mock.patch("os.listdir", return_value=files):
            responses = iter(["frog", "NaN", "-1", "0"])
            with mock.patch(
                    "click.prompt", side_effect=lambda p: next(responses)):
                with mock.patch(
                        "wormhole.cli.cmd_ssh.open",
                        return_value=pubkey_file):
                    res = cmd_ssh.find_public_key()
    assert len(res) == 3, res
    kind, keyid, pubkey = res
    assert kind == "ssh-rsa"
    assert keyid == "email@host"
    assert pubkey == pubkey_data

def test_comment_with_spaces():
    files = OTHERS + ["id_ed25519.pub", "id_ed25519"]
    pubkey_data = "ssh-ed25519 AAAAkeystuff comment with spaces"
    pubkey_file = io.StringIO(pubkey_data)

    with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
        with mock.patch("os.listdir", return_value=files) as ld:
            with mock.patch(
                    "wormhole.cli.cmd_ssh.open", return_value=pubkey_file):
                res = cmd_ssh.find_public_key()
    assert ld.mock_calls == \
                     [mock.call(os.path.expanduser("~/.ssh/"))]
    assert len(res) == 3, res
    kind, keyid, pubkey = res
    assert kind == "ssh-ed25519"
    assert keyid == "comment with spaces"
    assert pubkey == pubkey_data
