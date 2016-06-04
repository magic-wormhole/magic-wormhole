from __future__ import print_function, unicode_literals
import os, sys
from .cli_args import parser

def dispatch(args):
    if args.func == "server/start":
        from . import cmd_server
        return cmd_server.start_server(args)
    if args.func == "server/stop":
        from . import cmd_server
        return cmd_server.stop_server(args)
    if args.func == "server/restart":
        from . import cmd_server
        return cmd_server.restart_server(args)
    if args.func == "usage/usage":
        from . import cmd_usage
        return cmd_usage.show_usage(args)
    if args.func == "usage/tail":
        from . import cmd_usage
        return cmd_usage.tail_usage(args)
    if args.func == "usage/count-channels":
        from . import cmd_usage
        return cmd_usage.count_channels(args)
    if args.func == "usage/count-events":
        from . import cmd_usage
        return cmd_usage.count_events(args)

    raise ValueError("unknown args.func %s" % args.func)

def run(args, cwd, stdout, stderr, executable=None):
    """This is invoked directly by the 'wormhole-server' entry-point script.
    It can also invoked by entry() below."""

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
