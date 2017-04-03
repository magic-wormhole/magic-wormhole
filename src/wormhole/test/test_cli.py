from __future__ import print_function, absolute_import, unicode_literals
import io
from twisted.trial import unittest
from ..cli import welcome

class Welcome(unittest.TestCase):
    def do(self, welcome_message, my_version="2.0", twice=False):
        stderr = io.StringIO()
        h = welcome.CLIWelcomeHandler("url", my_version, stderr)
        h.handle_welcome(welcome_message)
        if twice:
            h.handle_welcome(welcome_message)
        return stderr.getvalue()

    def test_empty(self):
        stderr = self.do({})
        self.assertEqual(stderr, "")

    def test_version_current(self):
        stderr = self.do({"current_cli_version": "2.0"})
        self.assertEqual(stderr, "")

    def test_version_old(self):
        stderr = self.do({"current_cli_version": "3.0"})
        expected = ("Warning: errors may occur unless both sides are running the same version\n" +
                    "Server claims 3.0 is current, but ours is 2.0\n")
        self.assertEqual(stderr, expected)

    def test_version_old_twice(self):
        stderr = self.do({"current_cli_version": "3.0"}, twice=True)
        # the handler should only emit the version warning once, even if we
        # get multiple Welcome messages (which could happen if we lose the
        # connection and then reconnect)
        expected = ("Warning: errors may occur unless both sides are running the same version\n" +
                    "Server claims 3.0 is current, but ours is 2.0\n")
        self.assertEqual(stderr, expected)

    def test_version_unreleased(self):
        stderr = self.do({"current_cli_version": "3.0"},
                         my_version="2.5-middle-something")
        self.assertEqual(stderr, "")

    def test_motd(self):
        stderr = self.do({"motd": "hello"})
        self.assertEqual(stderr, "Server (at url) says:\n hello\n")
