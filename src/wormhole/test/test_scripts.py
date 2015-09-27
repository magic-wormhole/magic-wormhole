import os
from twisted.trial import unittest
from twisted.python import procutils, log
from twisted.internet.utils import getProcessOutputAndValue
from .. import __version__
from .common import ServerBase

class Scripts(ServerBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()
    def find_executable(self):
        locations = procutils.which("wormhole")
        if not locations:
            raise unittest.SkipTest("unable to find 'wormhole' in $PATH")
        wormhole = locations[0]
        here = os.path.dirname(os.path.abspath("."))
        if not os.path.abspath(wormhole).startswith(here):
            log.msg("locations: %s" % (locations,))
            log.msg("here: %s" % (here,))
            raise unittest.SkipTest("found the wrong 'wormhole' in $PATH: %s"
                                    % wormhole)
        return wormhole

    def test_version(self):
        # "wormhole" must be on the path, so e.g. "pip install -e ." in a
        # virtualenv
        wormhole = self.find_executable()
        d = getProcessOutputAndValue(wormhole, ["--version"])
        def _check(res):
            out, err, rc = res
            self.failUnlessEqual(out, "")
            self.failUnlessEqual(err, "magic-wormhole %s\n" % __version__)
            self.failUnlessEqual(rc, 0)
        d.addCallback(_check)
        return d

    def test_send_text_pre_generated_code(self):
        wormhole = self.find_executable()
        server_args = ["--relay-url", self.relayurl]
        code = "1-abc"
        message = "test message"
        send_args = server_args + [
            "send-text",
            "--code", code,
            message,
            ]
        d1 = getProcessOutputAndValue(wormhole, send_args)
        receive_args = server_args + [
            "receive-text",
            code,
            ]
        d2 = getProcessOutputAndValue(wormhole, receive_args)
        def _check_sender(res):
            out, err, rc = res
            self.failUnlessEqual(out,
                                 "On the other computer, please run: "
                                 "wormhole receive-text\n"
                                 "Wormhole code is: %s\n\n"
                                 "text sent\n" % code
                                 )
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            return d2
        d1.addCallback(_check_sender)
        def _check_receiver(res):
            out, err, rc = res
            self.failUnlessEqual(out, message+"\n")
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
        d1.addCallback(_check_receiver)
        return d1

    def test_send_file_pre_generated_code(self):
        code = "1-abc"
        message = "test message"

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        with open(os.path.join(send_dir, "testfile"), "w") as f:
            f.write(message)

        wormhole = self.find_executable()
        server_args = ["--relay-url", self.relayurl]
        send_args = server_args + [
            "send-file",
            "--code", code,
            "testfile",
            ]
        d1 = getProcessOutputAndValue(wormhole, send_args, path=send_dir)

        receive_dir = self.mktemp()
        os.mkdir(receive_dir)
        receive_args = server_args + [
            "receive-file",
            code,
            ]
        d2 = getProcessOutputAndValue(wormhole, receive_args, path=receive_dir)
        def _check_sender(res):
            out, err, rc = res
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive-file\n"
                              "Wormhole code is '%s'\n\n" % code,
                              out)
            self.failUnlessIn("File sent.. waiting for confirmation\n"
                              "Confirmation received. Transfer complete.\n",
                              out)
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            return d2
        d1.addCallback(_check_sender)
        def _check_receiver(res):
            out, err, rc = res
            self.failUnlessIn("Receiving %d bytes for 'testfile'" % len(message),
                              out)
            self.failUnlessIn("Received file written to ", out)
            self.failUnlessEqual(err, "")
            self.failUnlessEqual(rc, 0)
            fn = os.path.join(receive_dir, "testfile")
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), message)
        d1.addCallback(_check_receiver)
        return d1
