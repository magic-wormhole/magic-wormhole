import os, io
import mock
from twisted.trial import unittest
from ..cli import cmd_ssh

OTHERS = ["config", "config~", "known_hosts", "known_hosts~"]

class FindPubkey(unittest.TestCase):
    def test_find_one(self):
        files = OTHERS + ["id_rsa.pub", "id_rsa"]
        pubkey_data = u"ssh-rsa AAAAkeystuff email@host\n"
        pubkey_file = io.StringIO(pubkey_data)
        with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
            with mock.patch("os.listdir", return_value=files) as ld:
                with mock.patch("wormhole.cli.cmd_ssh.open",
                                return_value=pubkey_file):
                    res = cmd_ssh.find_public_key()
        self.assertEqual(ld.mock_calls,
                         [mock.call(os.path.expanduser("~/.ssh/"))])
        self.assertEqual(len(res), 3, res)
        kind, keyid, pubkey = res
        self.assertEqual(kind, "ssh-rsa")
        self.assertEqual(keyid, "email@host")
        self.assertEqual(pubkey, pubkey_data)

    def test_find_none(self):
        files = OTHERS # no pubkey
        with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
            with mock.patch("os.listdir", return_value=files):
                e = self.assertRaises(cmd_ssh.PubkeyError,
                                      cmd_ssh.find_public_key)
        dot_ssh = os.path.expanduser("~/.ssh/")
        self.assertEqual(str(e), "No public keys in '{}'".format(dot_ssh))

    def test_bad_hint(self):
        with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=False):
            e = self.assertRaises(cmd_ssh.PubkeyError,
                                  cmd_ssh.find_public_key,
                                  hint="bogus/path")
        self.assertEqual(str(e), "Can't find 'bogus/path'")


    def test_find_multiple(self):
        files = OTHERS + ["id_rsa.pub", "id_rsa", "id_dsa.pub", "id_dsa"]
        pubkey_data = u"ssh-rsa AAAAkeystuff email@host\n"
        pubkey_file = io.StringIO(pubkey_data)
        with mock.patch("wormhole.cli.cmd_ssh.exists", return_value=True):
            with mock.patch("os.listdir", return_value=files):
                responses = iter(["frog", "NaN", "-1", "0"])
                with mock.patch("click.prompt",
                                side_effect=lambda p: next(responses)):
                    with mock.patch("wormhole.cli.cmd_ssh.open",
                                    return_value=pubkey_file):
                        res = cmd_ssh.find_public_key()
        self.assertEqual(len(res), 3, res)
        kind, keyid, pubkey = res
        self.assertEqual(kind, "ssh-rsa")
        self.assertEqual(keyid, "email@host")
        self.assertEqual(pubkey, pubkey_data)
