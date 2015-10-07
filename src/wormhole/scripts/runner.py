from __future__ import print_function
import sys, argparse
from textwrap import dedent
from .. import public_relay
from .. import __version__
from . import cmd_send, cmd_receive
from ..servers import cmd_server

parser = argparse.ArgumentParser(
    usage="wormhole SUBCOMMAND (subcommand-options)",
    description=dedent("""
    Create a Magic Wormhole and communicate through it. Wormholes are created
    by speaking the same magic CODE in two different places at the same time.
    Wormholes are secure against anyone who doesn't use the same code."""),
    )

parser.add_argument("--version", action="version",
                    version="magic-wormhole "+ __version__)
g = parser.add_argument_group("wormhole configuration options")
g.add_argument("--relay-url", default=public_relay.RENDEZVOUS_RELAY,
               metavar="URL", help="rendezvous relay to use", type=type(u""))
g.add_argument("--transit-helper", default=public_relay.TRANSIT_RELAY,
               metavar="tcp:HOST:PORT", help="transit relay to use",
               type=type(u""))
g.add_argument("-c", "--code-length", type=int, default=2,
               metavar="WORDS", help="length of code (in bytes/words)")
g.add_argument("-v", "--verify", action="store_true",
               help="display (and wait for acceptance of) verification string")
subparsers = parser.add_subparsers(title="subcommands",
                                   dest="subcommand")


# CLI: run-server
s = subparsers.add_parser("server", description="Start/stop a relay server")
sp = s.add_subparsers(title="subcommands", dest="subcommand")
sp_start = sp.add_parser("start", description="Start a relay server",
                         usage="wormhole server start [opts] [TWISTD-ARGS..]")
sp_start.add_argument("--rendezvous", default="tcp:3000", metavar="tcp:PORT",
                      help="endpoint specification for the rendezvous port")
sp_start.add_argument("--transit", default="tcp:3001", metavar="tcp:PORT",
                      help="endpoint specification for the transit-relay port")
sp_start.add_argument("--advertise-version", metavar="VERSION",
                      help="version to recommend to clients")
sp_start.add_argument("-n", "--no-daemon", action="store_true")
#sp_start.add_argument("twistd_args", nargs="*", default=None,
#                      metavar="[TWISTD-ARGS..]",
#                      help=dedent("""\
#                      Additional arguments to pass to twistd"""),
#                      )
sp_start.set_defaults(func=cmd_server.start_server)

sp_stop = sp.add_parser("stop", description="Stop the relay server",
                        usage="wormhole server stop")
sp_stop.set_defaults(func=cmd_server.stop_server)

sp_restart = sp.add_parser("restart", description="Restart the relay server",
                           usage="wormhole server restart")
sp_restart.add_argument("--rendezvous", default="tcp:3000", metavar="tcp:PORT",
                        help="endpoint specification for the rendezvous port")
sp_restart.add_argument("--transit", default="tcp:3001", metavar="tcp:PORT",
                        help="endpoint specification for the transit-relay port")
sp_restart.add_argument("--advertise-version", metavar="VERSION",
                        help="version to recommend to clients")
sp_restart.set_defaults(func=cmd_server.restart_server)

# CLI: send
p = subparsers.add_parser("send",
                          description="Send text message or file",
                          usage="wormhole send [FILENAME]")
p.add_argument("--text", metavar="MESSAGE",
               help="text message to send, instead of a file")
p.add_argument("--code", metavar="CODE", help="human-generated code phrase",
               type=type(u""))
p.add_argument("-0", dest="zeromode", action="store_true",
               help="enable no-code anything-goes mode")
p.add_argument("what", nargs="?", default=None, metavar="[FILENAME]",
               help="the file to send")
p.set_defaults(func=cmd_send.send)

# CLI: receive
p = subparsers.add_parser("receive",
                          description="Receive a text message or file",
                          usage="wormhole receive [CODE]")
p.add_argument("-0", dest="zeromode", action="store_true",
               help="enable no-code anything-goes mode")
p.add_argument("-t", "--only-text", dest="only_text", action="store_true",
               help="refuse file transfers, only accept text transfers")
p.add_argument("--accept-file", dest="accept_file", action="store_true",
               help="accept file transfer with asking for confirmation")
p.add_argument("-o", "--output-file", default=None, metavar="FILENAME",
               help=dedent("""\
               The file to create, overriding the filename suggested by the
               sender."""),
               )
p.add_argument("code", nargs="?", default=None, metavar="[CODE]",
               help=dedent("""\
               The magic-wormhole code, from the sender. If omitted, the
               program will ask for it, using tab-completion."""),
               type=type(u""),
               )
p.set_defaults(func=cmd_receive.receive)



def run(args, stdout, stderr, executable=None):
    """This is invoked directly by the 'wormhole' entry-point script. It can
    also invoked by entry() below."""

    args = parser.parse_args()
    if not getattr(args, "func", None):
        # So far this only works on py3. py2 exits with a really terse
        # "error: too few arguments" during parse_args().
        parser.print_help()
        sys.exit(0)
    try:
        #rc = command.func(args, stdout, stderr)
        rc = args.func(args)
        return rc
    except ImportError as e:
        print("--- ImportError ---", file=stderr)
        print(e, file=stderr)
        print("Please run 'python setup.py build'", file=stderr)
        raise
        return 1

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    return run(sys.argv[1:], sys.stdout, sys.stderr, executable=sys.argv[0])

if __name__ == "__main__":
    args = parser.parse_args()
    print(args)
