from __future__ import print_function
import os, sys
from twisted.internet.defer import maybeDeferred
from twisted.internet.task import react
from ..timing import DebugTiming
from .cli_args import parser

def dispatch(args): # returns Deferred
    if args.func == "send/send":
        from . import cmd_send
        return cmd_send.send_twisted(args)
    if args.func == "receive/receive":
        _start = args.timing.add_event("import c_r_t")
        from . import cmd_receive
        args.timing.finish_event(_start)
        return cmd_receive.receive_twisted(args)

    raise ValueError("unknown args.func %s" % args.func)

def run(reactor, argv, cwd, stdout, stderr, executable=None):
    """This is invoked by entry() below, and can also be invoked directly by
    tests.
    """

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # So far this only works on py3. py2 exits with a really terse
        # "error: too few arguments" during parse_args().
        parser.print_help()
        sys.exit(0)
    args.cwd = cwd
    args.stdout = stdout
    args.stderr = stderr
    args.timing = timing = DebugTiming()

    timing.add_event("command dispatch")
    d = maybeDeferred(dispatch, args)
    def _maybe_dump_timing(res):
        timing.add_event("exit")
        if args.dump_timing:
            timing.write(args.dump_timing, stderr)
        return res
    d.addBoth(_maybe_dump_timing)
    def _rc(rc):
        raise SystemExit(rc)
    d.addCallback(_rc)
    return d

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    react(run, (sys.argv[1:], os.getcwd(), sys.stdout, sys.stderr,
                sys.argv[0]))

if __name__ == "__main__":
    args = parser.parse_args()
    print(args)
