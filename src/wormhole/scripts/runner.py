from __future__ import print_function
import os, sys
from ..errors import TransferError
from .cli_args import parser

def dispatch(args):
    if args.func == "server/start":
        from ..servers import cmd_server
        return cmd_server.start_server(args)
    if args.func == "server/stop":
        from ..servers import cmd_server
        return cmd_server.stop_server(args)
    if args.func == "server/restart":
        from ..servers import cmd_server
        return cmd_server.restart_server(args)
    if args.func == "usage/usage":
        from ..servers import cmd_usage
        return cmd_usage.show_usage(args)
    if args.func == "usage/tail":
        from ..servers import cmd_usage
        return cmd_usage.tail_usage(args)
    if args.func == "send/send":
        from . import cmd_send_blocking
        return cmd_send_blocking.send_blocking(args)
    if args.func == "receive/receive":
        from . import cmd_receive_blocking
        return cmd_receive_blocking.receive_blocking(args)
    raise ValueError("unknown args.func %s" % args.func)

def run(args, cwd, stdout, stderr, executable=None):
    """This is invoked directly by the 'wormhole' entry-point script. It can
    also invoked by entry() below."""

    args = parser.parse_args()
    if not getattr(args, "func", None):
        # So far this only works on py3. py2 exits with a really terse
        # "error: too few arguments" during parse_args().
        parser.print_help()
        sys.exit(0)
    args.cwd = cwd
    args.stdout = stdout
    args.stderr = stderr
    try:
        rc = dispatch(args)
        return rc
    except TransferError as e:
        print(e, file=stderr)
        return 1
    except ImportError as e:
        print("--- ImportError ---", file=stderr)
        print(e, file=stderr)
        print("Please run 'python setup.py build'", file=stderr)
        raise
        return 1

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    return run(sys.argv[1:], os.getcwd(), sys.stdout, sys.stderr,
               executable=sys.argv[0])

if __name__ == "__main__":
    args = parser.parse_args()
    print(args)
