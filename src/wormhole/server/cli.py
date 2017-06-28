from __future__ import print_function
import os, sys
from twisted.python import usage
from twisted.scripts.twistd import run

class StartOptions(usage.Options):
    # replace the normal parseOptions with one that just records everything,
    # since we want "wormhole-server start ARGS" to build a "twistd
    # wormhole-server ARGS" without complaining that StartOptions doesn't
    # understand things like --blur-usage. We want to leave these args as
    # strings. Besides, to parse them here we'd need to copy everything from
    # .service.Options .
    def parseOptions(self, options):
        self._extra_args = tuple(options)

class StopOptions(usage.Options):
    pass

class Options(usage.Options):
    subCommands = [
        ("start", None, StartOptions, "Start a server"),
        ("stop", None, StopOptions, "Stop a running server"),
        ]

def server():
    config = Options()
    config.parseOptions() # uses sys.argv[1:]
    if config.subCommand == "start":
        sys.argv = ("twistd", "wormhole-server") + config.subOptions._extra_args
        run()
        print("should never get here")
    elif config.subCommand == "stop":
        pidfile = os.path.join(os.getcwd(), "twistd.pid")
        from .service import stop_and_wait
        stop_and_wait(pidfile)
    sys.exit(0)
