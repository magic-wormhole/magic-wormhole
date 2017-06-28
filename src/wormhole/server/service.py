# twist/twistd will load this file and look for 'Options' and 'makeService'

from twisted.python import usage
from .server import RelayServer

class Options(usage.Options):
    optFlags = [
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

def makeService(config):
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

