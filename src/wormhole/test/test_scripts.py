import os, sys, re
from twisted.trial import unittest
from twisted.python import procutils, log
from twisted.internet.utils import getProcessOutputAndValue
from twisted.internet.defer import inlineCallbacks
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

class PregeneratedCode(ServerBase, ScriptsBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    def setUp(self):
        d = self.is_runnable()
        d.addCallback(lambda _: ServerBase.setUp(self))
        return d

    @inlineCallbacks
    def _do_test(self, mode="text", override_filename=False):
        assert mode in ("text", "file", "directory")
        wormhole = self.find_executable()
        common_args = ["--hide-progress",
                       "--relay-url", self.relayurl,
                       "--transit-helper", ""]
        code = u"1-abc"
        message = "test message"

        send_args = common_args + [
            "send",
            "--code", code,
            ]

        receive_args = common_args + [
            "receive",
            ]

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        receive_dir = self.mktemp()
        os.mkdir(receive_dir)

        if mode == "text":
            send_args.extend(["--text", message])

        elif mode == "file":
            send_filename = "testfile"
            with open(os.path.join(send_dir, send_filename), "w") as f:
                f.write(message)
            send_args.append(send_filename)
            receive_filename = send_filename

            receive_args.append("--accept-file")
            if override_filename:
                receive_args.extend(["-o", "outfile"])
                receive_filename = "outfile"

        elif mode == "directory":
            # $send_dir/
            # $send_dir/middle/
            # $send_dir/middle/$dirname/
            # $send_dir/middle/$dirname/[12345]
            # cd $send_dir && wormhole send middle/$dirname
            # cd $receive_dir && wormhole receive
            # expect: $receive_dir/$dirname/[12345]

            send_dirname = "testdir"
            def message(i):
                return "test message %d\n" % i
            os.mkdir(os.path.join(send_dir, "middle"))
            source_dir = os.path.join(send_dir, "middle", send_dirname)
            os.mkdir(source_dir)
            for i in range(5):
                with open(os.path.join(source_dir, str(i)), "w") as f:
                    f.write(message(i))
            send_args.append(os.path.join("middle", send_dirname))
            receive_dirname = send_dirname

            receive_args.append("--accept-file")
            if override_filename:
                receive_args.extend(["-o", "outdir"])
                receive_dirname = "outdir"

        receive_args.append(code)

        send_d = getProcessOutputAndValue(wormhole, send_args, path=send_dir)
        receive_d = getProcessOutputAndValue(wormhole, receive_args,
                                             path=receive_dir)

        self.maxDiff = None # show full output for assertion failures

        # check sender
        send_res = yield send_d
        out, err, rc = send_res
        out = out.decode("utf-8")
        err = err.decode("utf-8")
        self.failUnlessEqual(err, "")
        if mode == "text":
            expected = ("Sending text message (%d bytes)\n"
                        "On the other computer, please run: "
                        "wormhole receive\n"
                        "Wormhole code is: %s\n\n"
                        "text message sent\n" % (len(message), code))
            self.failUnlessEqual(out, expected)
        elif mode == "file":
            self.failUnlessIn("Sending %d byte file named '%s'\n" %
                              (len(message), send_filename), out)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive\n"
                              "Wormhole code is: %s\n\n" % code,
                              out)
            self.failUnlessIn("File sent.. waiting for confirmation\n"
                              "Confirmation received. Transfer complete.\n",
                              out)
        elif mode == "directory":
            self.failUnlessIn("Sending directory", out)
            self.failUnlessIn("named 'testdir'", out)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive\n"
                              "Wormhole code is: %s\n\n" % code,
                              out)
            self.failUnlessIn("File sent.. waiting for confirmation\n"
                              "Confirmation received. Transfer complete.\n",
                              out)
        self.failUnlessEqual(rc, 0)

        # check receiver
        receive_res = yield receive_d
        out, err, rc = receive_res
        out = out.decode("utf-8")
        err = err.decode("utf-8")
        if mode == "text":
            self.failUnlessEqual(out, message+"\n")
        elif mode == "file":
            self.failUnlessIn("Receiving %d bytes for '%s'" %
                              (len(message), receive_filename), out)
            self.failUnlessIn("Received file written to ", out)
            fn = os.path.join(receive_dir, receive_filename)
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), message)
        elif mode == "directory":
            self.failUnless(re.search(r"Receiving \d+ bytes for '%s'" %
                                      receive_dirname, out))
            self.failUnlessIn("Received files written to %s" %
                              receive_dirname, out)
            fn = os.path.join(receive_dir, receive_dirname)
            self.failUnless(os.path.exists(fn))
            for i in range(5):
                fn = os.path.join(receive_dir, receive_dirname, str(i))
                with open(fn, "r") as f:
                    self.failUnlessEqual(f.read(), message(i))
        self.failUnlessEqual(err, "")
        self.failUnlessEqual(rc, 0)

    def test_text(self):
        return self._do_test()

    def test_file(self):
        return self._do_test(mode="file")
    def test_file_override(self):
        return self._do_test(mode="file", override_filename=True)

    def test_directory(self):
        return self._do_test(mode="directory")
    def test_directory_override(self):
        return self._do_test(mode="directory", override_filename=True)
