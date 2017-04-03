from __future__ import print_function, absolute_import, unicode_literals
import sys
from ..wormhole import _WelcomeHandler

class CLIWelcomeHandler(_WelcomeHandler):
    def __init__(self, url, cli_version, stderr=sys.stderr):
        _WelcomeHandler.__init__(self, url, stderr)
        self._current_version = cli_version
        self._version_warning_displayed = False

    def handle_welcome(self, welcome):
        # Only warn if we're running a release version (e.g. 0.0.6, not
        # 0.0.6-DISTANCE-gHASH). Only warn once.
        if ("current_cli_version" in welcome
            and "-" not in self._current_version
            and not self._version_warning_displayed
            and welcome["current_cli_version"] != self._current_version):
            print("Warning: errors may occur unless both sides are running the same version", file=self.stderr)
            print("Server claims %s is current, but ours is %s"
                  % (welcome["current_cli_version"], self._current_version),
                  file=self.stderr)
            self._version_warning_displayed = True
        _WelcomeHandler.handle_welcome(self, welcome)

