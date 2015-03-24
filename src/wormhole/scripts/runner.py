import sys, argparse
from textwrap import dedent
from .. import public_relay
from .. import __version__
from . import cmd_send_text, cmd_receive_text, cmd_send_file, cmd_receive_file

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
               metavar="URL", help="rendezvous relay to use")
g.add_argument("--transit-helper", default=public_relay.TRANSIT_RELAY,
               metavar="tcp:HOST:PORT", help="transit relay to use")
g.add_argument("-c", "--code-length", type=int, default=2,
               metavar="WORDS", help="length of code (in bytes/words)")
g.add_argument("-v", "--verify", action="store_true",
               help="display (and wait for acceptance of) verification string")
subparsers = parser.add_subparsers(title="subcommands",
                                   dest="subcommand")


p = subparsers.add_parser("send-text", description="Send a text mesasge",
                          usage="wormhole send-text TEXT")
p.add_argument("text", metavar="TEXT", help="the message to send (a string)")
p.set_defaults(func=cmd_send_text.send_text)

p = subparsers.add_parser("receive-text", description="Receive a text message",
                          usage="wormhole receive-text [CODE]")
p.add_argument("code", nargs="?", default=None, metavar="[CODE]",
               help=dedent("""\
               The magic-wormhole code, from the sender. If omitted, the
               program will ask for it, using tab-completion."""),
               )
p.set_defaults(func=cmd_receive_text.receive_text)

p = subparsers.add_parser("send-file", description="Send a file",
                          usage="wormhole send-file FILENAME")
p.add_argument("filename", metavar="FILENAME", help="The file to be sent")
p.set_defaults(func=cmd_send_file.send_file)

p = subparsers.add_parser("receive-file", description="Receive a file",
                          usage="wormhole receive-file [-o FILENAME] [CODE]")
p.add_argument("-o", "--output-file", default=None, metavar="FILENAME",
               help=dedent("""\
               The file to create, overriding the filename suggested by the
               sender"""),
               )
p.add_argument("code", nargs="?", default=None, metavar="[CODE]",
               help=dedent("""\
               The magic-wormhole code, from the sender. If omitted, the
               program will ask for it, using tab-completion."""),
               )
p.set_defaults(func=cmd_receive_file.receive_file)


def run(args, stdout, stderr, executable=None):
    """This is invoked directly by the 'wormhole' entry-point script. It can
    also invoked by entry() below."""

    args = parser.parse_args()
    try:
        #rc = command.func(args, stdout, stderr)
        rc = args.func(args)
        return rc
    except ImportError, e:
        print >>stderr, "--- ImportError ---"
        print >>stderr, e
        print >>stderr, "Please run 'python setup.py build'"
        raise
        return 1

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    return run(sys.argv[1:], sys.stdout, sys.stderr, executable=sys.argv[0])

if __name__ == "__main__":
    args = parser.parse_args()
    print args
