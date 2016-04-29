from __future__ import print_function
import time
start = time.time()
import os, sys
from twisted.internet.defer import maybeDeferred
from twisted.internet.task import react
from ..errors import TransferError, WrongPasswordError, Timeout
from ..timing import DebugTiming
from .cli_args import parser
top_import_finish = time.time()

def dispatch(args): # returns Deferred
    if args.func == "send/send":
        with args.timing.add("import", which="cmd_send"):
            from . import cmd_send
        return cmd_send.send(args)
    if args.func == "receive/receive":
        with args.timing.add("import", which="cmd_receive"):
            from . import cmd_receive
        return cmd_receive.receive(args)

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

    timing.add("command dispatch")
    timing.add("import", when=start, which="top").finish(when=top_import_finish)
    # fires with None, or raises an error
    d = maybeDeferred(dispatch, args)
    def _maybe_dump_timing(res):
        timing.add("exit")
        if args.dump_timing:
            timing.write(args.dump_timing, stderr)
        return res
    d.addBoth(_maybe_dump_timing)
    def _explain_error(f):
        # these three errors don't print a traceback, just an explanation
        f.trap(TransferError, WrongPasswordError, Timeout)
        print("ERROR:", f.value, file=stderr)
        raise SystemExit(1)
    d.addErrback(_explain_error)
    d.addCallback(lambda _: 0)
    return d

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    react(run, (sys.argv[1:], os.getcwd(), sys.stdout, sys.stderr,
                sys.argv[0]))

if __name__ == "__main__":
    args = parser.parse_args()
    print(args)
