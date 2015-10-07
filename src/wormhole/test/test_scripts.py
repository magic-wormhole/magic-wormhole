import os, sys
from twisted.trial import unittest
from twisted.python import procutils, log
from twisted.internet.utils import getProcessOutputAndValue
from .. import __version__
from .common import ServerBase

class ScriptsBase:
    def find_executable(self):
        # to make sure we're running the right executable (in a virtualenv),
        # we require that our "wormhole" lives in the same directory as our
        # "python"
        locations = procutils.which("wormhole")
        if not locations:
            raise unittest.SkipTest("unable to find 'wormhole' in $PATH")
        wormhole = locations[0]
        if (os.path.dirname(os.path.abspath(wormhole)) !=
            os.path.dirname(sys.executable)):
            log.msg("locations: %s" % (locations,))
            log.msg("sys.executable: %s" % (sys.executable,))
            raise unittest.SkipTest("found the wrong 'wormhole' in $PATH: %s %s"
                                    % (wormhole, sys.executable))
        return wormhole

    def is_runnable(self):
        # One property of Versioneer is that many changes to the source tree
        # (making a commit, dirtying a previously-clean tree) will change the
        # version string. Entrypoint scripts frequently insist upon importing
        # a library version that matches the script version (whatever was
        # reported when 'pip install' was run), and throw a
        # DistributionNotFound error when they don't match. This is really
        # annoying in a workspace created with "pip install -e .", as you
        # must re-run pip after each commit.

        # So let's report just one error in this case (from test_version),
        # and skip the other tests that we know will fail.

        wormhole = self.find_executable()
        d = getProcessOutputAndValue(wormhole, ["--version"])
        def _check(res):
            out, err, rc = res
            if rc != 0:
                raise unittest.SkipTest("wormhole is not runnable in this tree")
        d.addCallback(_check)
        return d

class ScriptVersion(ServerBase, ScriptsBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def test_version(self):
        # "wormhole" must be on the path, so e.g. "pip install -e ." in a
        # virtualenv. This guards against an environment where the tests
        # below might run the wrong executable.
        wormhole = self.find_executable()
        d = getProcessOutputAndValue(wormhole, ["--version"])
        def _check(res):
            out, err, rc = res
            # argparse on py2 and py3.3 sends --version to stderr
            # argparse on py3.4/py3.5 sends --version to stdout
            # aargh
            err = err.decode("utf-8")
            if "DistributionNotFound" in err:
                log.msg("stderr was %s" % err)
                last = err.strip().split("\n")[-1]
                self.fail("wormhole not runnable: %s" % last)
            ver = out.decode("utf-8") or err
            self.failUnlessEqual(ver, "magic-wormhole %s\n" % __version__)
            self.failUnlessEqual(rc, 0)
        d.addCallback(_check)
        return d

class Scripts(ServerBase, ScriptsBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def setUp(self):
        d = self.is_runnable()
        d.addCallback(lambda _: ServerBase.setUp(self))
        return d

    def test_send_text_pre_generated_code(self):
        wormhole = self.find_executable()
        server_args = ["--relay-url", self.relayurl]
        code = u"1-abc"
        message = "test message"
        send_args = server_args + [
            "send",
            "--code", code,
            "--text", message,
            ]
        d1 = getProcessOutputAndValue(wormhole, send_args)
        receive_args = server_args + [
            "receive",
            code,
            ]
        d2 = getProcessOutputAndValue(wormhole, receive_args)
        def _check_sender(res):
            out, err, rc = res
            out = out.decode("utf-8")
            err = err.decode("utf-8")
            self.maxDiff = None
            expected = ("Sending text message (%d bytes)\n"
                        "On the other computer, please run: "
                        "wormhole receive\n"
                        "Wormhole code is: %s\n\n"
                        "text message sent\n" % (len(message), code))
            self.failUnlessEqual( (expected, "", 0),
                                  (out, err, rc) )
            return d2
        d1.addCallback(_check_sender)
        def _check_receiver(res):
            out, err, rc = res
            out = out.decode("utf-8")
            err = err.decode("utf-8")
            self.failUnlessEqual( (message+"\n", "", 0),
                                  (out, err, rc) )
        d1.addCallback(_check_receiver)
        return d1

    def test_send_file_pre_generated_code(self):
        self.maxDiff=None
        code = u"1-abc"
        filename = "testfile"
        message = "test message"

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        with open(os.path.join(send_dir, filename), "w") as f:
            f.write(message)

        wormhole = self.find_executable()
        server_args = ["--relay-url", self.relayurl]
        send_args = server_args + [
            "send",
            "--code", code,
            filename,
            ]
        d1 = getProcessOutputAndValue(wormhole, send_args, path=send_dir)

        receive_dir = self.mktemp()
        os.mkdir(receive_dir)
        receive_args = server_args + [
            "receive", "--accept-file",
            code,
            ]
        d2 = getProcessOutputAndValue(wormhole, receive_args, path=receive_dir)
        def _check_sender(res):
            out, err, rc = res
            out = out.decode("utf-8")
            err = err.decode("utf-8")
            self.failUnlessEqual(err, "")
            self.failUnlessIn("Sending %d byte file named '%s'\n" %
                              (len(message), filename), out)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive\n"
                              "Wormhole code is: %s\n\n" % code,
                              out)
            self.failUnlessIn("File sent.. waiting for confirmation\n"
                              "Confirmation received. Transfer complete.\n",
                              out)
            self.failUnlessEqual(rc, 0)
            return d2
        d1.addCallback(_check_sender)
        def _check_receiver(res):
            out, err, rc = res
            out = out.decode("utf-8")
            err = err.decode("utf-8")
            self.failUnlessIn("Receiving %d bytes for '%s'" %
                              (len(message), filename), out)
            self.failUnlessIn("Received file written to ", out)
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            fn = os.path.join(receive_dir, filename)
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), message)
        d1.addCallback(_check_receiver)
        return d1
