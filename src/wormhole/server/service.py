# twist/twistd will load this file and look for 'Options' and 'makeService'

import time, os.path
from twisted.python import usage
from .server import RelayServer

class Options(usage.Options):
    optFlags = [
        ("restart-first", None,
         "stop the old server before starting a new one"),
        ("disallow-list", None,
         "always/never send list of allocated nameplates"),
        ]
    optParameters = [
        ("rendezvous", None, "tcp:4000",
         "endpoint specification for the rendezvous port"),
        ("transit", None, "tcp:4001",
         "endpoint specification for the transit-relay port"),
        ("advertise-version", None, None,
         "version to recommend to clients"),
        ("blur-usage", None, None,
         "round logged access times to improve privacy"),
        ("signal-error", None, None,
         "force all clients to fail with a message"),
        ("relay-database-path", None, "relay.sqlite",
         "location for the relay server state database"),
        ("stats-json-path", None, "stats.json",
         "location to write the relay stats file"),
        ]

    def postOptions(self):
        if self["restart-first"]:
            self._old_pidfile = self.parent["pidfile"]
            self.parent["pidfile"] = "bypassed-pidfile"

class TimeoutError(Exception):
    def __init__(self, pid, service_dir):
        self._pid = pid
        self._service_dir = service_dir

    def __str__(self):
        return ("pid %d in %s still running after 10 seconds, giving up"
                % (self._pid, self._service_dir))


def stop_and_wait(pidfile):
    service_dir = os.path.dirname(os.path.realpath(pidfile))
    try:
        with open(pidfile, "r") as f:
            pid = int(f.read().strip())
    except EnvironmentError:
        print("Unable to find PID file: is this really a server directory?")
        print("ignoring --restart-first")
        return
    print("sending SIGTERM to pid %d in %s" % (pid, service_dir))
    os.kill(pid, 15)
    print("waiting for process to exit")
    timeout = time.time() + 10.0
    while time.time() < timeout:
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            print("pid %d has exited" % pid)
            return
    raise TimeoutError(pid, service_dir)

def makeService(config):
    if config["restart-first"]:
        pidfile = config._old_pidfile
        config.parent["pidfile"] = pidfile
        stop_and_wait(pidfile)
    s = RelayServer(
        str(config["rendezvous"]),
        str(config["transit"]),
        config["advertise-version"],
        config["relay-database-path"],
        None if config["blur-usage"] is None else int(config["blur-usage"]),
        signal_error=config["signal-error"],
        stats_file=config["stats-json-path"],
        allow_list=(not config["disallow-list"]),
        )
    return s

