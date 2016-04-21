import argparse
from textwrap import dedent
from .. import __version__

parser = argparse.ArgumentParser(
    usage="wormhole-server SUBCOMMAND (subcommand-options)",
    description=dedent("""
    Create a Magic Wormhole and communicate through it. Wormholes are created
    by speaking the same magic CODE in two different places at the same time.
    Wormholes are secure against anyone who doesn't use the same code."""),
    )

parser.add_argument("--version", action="version",
                    version="magic-wormhole "+ __version__)
s = parser.add_subparsers(title="subcommands", dest="subcommand")


# CLI: run-server
sp_start = s.add_parser("start", description="Start a relay server",
                        usage="wormhole server start [opts] [TWISTD-ARGS..]")
sp_start.add_argument("--rendezvous", default="tcp:3000", metavar="tcp:PORT",
                      help="endpoint specification for the rendezvous port")
sp_start.add_argument("--transit", default="tcp:3001", metavar="tcp:PORT",
                      help="endpoint specification for the transit-relay port")
sp_start.add_argument("--advertise-version", metavar="VERSION",
                      help="version to recommend to clients")
sp_start.add_argument("--blur-usage", default=None, type=int,
                      metavar="SECONDS",
                      help="round logged access times to improve privacy")
sp_start.add_argument("-n", "--no-daemon", action="store_true")
#sp_start.add_argument("twistd_args", nargs="*", default=None,
#                      metavar="[TWISTD-ARGS..]",
#                      help=dedent("""\
#                      Additional arguments to pass to twistd"""),
#                      )
sp_start.set_defaults(func="server/start")

sp_stop = s.add_parser("stop", description="Stop the relay server",
                       usage="wormhole server stop")
sp_stop.set_defaults(func="server/stop")

sp_restart = s.add_parser("restart", description="Restart the relay server",
                          usage="wormhole server restart")
sp_restart.add_argument("--rendezvous", default="tcp:3000", metavar="tcp:PORT",
                        help="endpoint specification for the rendezvous port")
sp_restart.add_argument("--transit", default="tcp:3001", metavar="tcp:PORT",
                        help="endpoint specification for the transit-relay port")
sp_restart.add_argument("--advertise-version", metavar="VERSION",
                        help="version to recommend to clients")
sp_restart.add_argument("--blur-usage", default=None, type=int,
                        metavar="SECONDS",
                        help="round logged access times to improve privacy")
sp_restart.add_argument("-n", "--no-daemon", action="store_true")
sp_restart.set_defaults(func="server/restart")

sp_show_usage = s.add_parser("show-usage", description="Display usage data",
                             usage="wormhole server show-usage")
sp_show_usage.add_argument("-n", default=100, type=int,
                           help="show last N entries")
sp_show_usage.set_defaults(func="usage/usage")

sp_tail_usage = s.add_parser("tail-usage", description="Follow latest usage",
                             usage="wormhole server tail-usage")
sp_tail_usage.set_defaults(func="usage/tail")
