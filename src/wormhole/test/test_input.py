from __future__ import print_function, absolute_import, unicode_literals
import os, sys, pty
import mock
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import gatherResults, inlineCallbacks
from twisted.internet.threads import deferToThread, blockingCallFromThread
from .common import ServerBase
from .. import wormhole
from ..errors import LonelyError

APPID = "appid"

class Completion2(ServerBase, unittest.TestCase):
    def setUp(self):
        self._clients = []
        return ServerBase.setUp(self)

    @inlineCallbacks
    def tearDown(self):
        for c in self._clients:
            yield self.assertFailure(w.close(), LonelyError)

    def add_nameplate(self, nameplate):
        code = "%d-abc" % nameplate
        w = wormhole.create(APPID, self.relayurl, reactor)
        # we want to wait until the server knows about the nameplate
        w._N._debug_when_claimed_d = d = Deferred()
        w.set_code(code)
        yield d
        # that Deferred will fire while the Wormhole is in the middle of a
        # state transition, so it isn't really safe to do anything else with
        # that instance yet. This particular test will spawn a subprocess
        # before doing anything else, which should provide enough delay to
        # let the wormhole clean up. All we really care about is the server
        # returning the right set of nameplates anyways.
del Completion2

import six

# python-3.6.1/Python/bltinmodule.c builtin_input_impl() says "we should only
# use (GNU) readline if Python's sys.stdin and sys.stdout are the same as C's
# stdin and stdout, because we need to pass it those", which pretty much
# clobbers my idea of moving ptys into place for local input() tests. Changed
# in cpython eba769657a32cb08d96f021f40c79a54ade0bffc, first appeared in 3.0

# maybe os.dup2 0 and 1 out of the way, close them, re-open them as ptys,
# then move everything back when we're done?

def get_input():
    return six.moves.input("prompt")
    

class Completion(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_input(self):
        f = open("debug.out", "w")
        f.write("starting\n"); f.flush()
        import readline
        if 1:
            pi = pty.openpty()
            print("pi", pi)
            stdin_writer = os.fdopen(pi[0], "w")
            stdin_reader = os.fdopen(pi[1], "r")

            po = pty.openpty()
            print("po", po)
            stdout_reader = os.fdopen(po[0], "r")
            stdout_writer = os.fdopen(po[1], "w")

        if 0:
            p = pty.openpty()
            stdin_writer = os.fdopen(p[0], "w")
            stdout_reader = os.fdopen(p[0], "r")
            stdin_reader = os.fdopen(p[1], "r")
            stdout_writer = os.fdopen(p[1], "w")

        if 1:
            stdin_fileno = sys.stdin.fileno()
            stdout_fileno = sys.stdout.fileno()
            stdin_mode = sys.stdin.mode
            stdout_mode = sys.stdout.mode
            preserved_stdin = os.fdopen(os.dup(stdin_fileno), stdin_mode)
            preserved_stdout = os.fdopen(os.dup(stdout_fileno), stdout_mode)
            f.write("1\n"); f.flush()
            #sys.stdin.close()
            #sys.stdout.close()
            f.write("2\n"); f.flush()
            os.dup2(stdin_reader.fileno(), stdin_fileno)
            f.write("2.5\n"); f.flush()
            os.dup2(stdout_writer.fileno(), stdout_fileno)
            f.write("3\n"); f.flush()
            sys.stdin = os.fdopen(stdin_fileno, stdin_mode)
            sys.stdout = os.fdopen(stdout_fileno, stdout_mode)
            f.write("4\n"); f.flush()
        f.write("ready\n"); f.flush()
        
        #stdout_writer = open("/dev/null", "w")

        #real_stdin = sys.stdin
        #real_stdout = sys.stdout
        try:
            #sys.stdin = stdin_reader
            #sys.stdout = stdout_writer
            f.write("starting thread\n"); f.flush()
            d = deferToThread(get_input)
            stdin_writer.write("hello")
            stdin_writer.flush()
            stdin_writer.write("\n")
            stdin_writer.flush()
            #stdin_writer.close()
            f.write("wrote, waiting\n"); f.flush()
            # need to read from stdout now: make a Protocol, like the top
            # half of ProcessProtocol
            v = yield d
        finally:
            f.write("starting cleanup\n"); f.flush()
            sys.stdin.close()
            sys.stdout.close()
            os.dup2(preserved_stdin.fileno(), stdin_fileno)
            sys.stdin = os.fdopen(stdin_fileno, stdin_mode)
            os.dup2(preserved_stdout.fileno(), stdout_fileno)
            sys.stdout = os.fdopen(stdout_fileno, stdout_mode)
            f.write("done cleanup\n"); f.flush()
        print()
        print("v", v)
    timeout = 10
