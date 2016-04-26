from __future__ import print_function
import os, sys, re, io, zipfile, six
from twisted.trial import unittest
from twisted.python import procutils, log
from twisted.internet.utils import getProcessOutputAndValue
from twisted.internet.defer import gatherResults, inlineCallbacks
from .. import __version__
from .common import ServerBase
from ..cli import runner, cmd_send, cmd_receive
from ..cli.cmd_send import build_phase1_data
from ..errors import TransferError, WrongPasswordError
from ..timing import DebugTiming

class Phase1Data(unittest.TestCase):
    def setUp(self):
        self._things_to_delete = []

    def tearDown(self):
        for fn in self._things_to_delete:
            if os.path.exists(fn):
                os.unlink(fn)

    def test_text(self):
        message = "blah blah blah ponies"

        send_args = [ "send", "--text", message ]
        args = runner.parser.parse_args(send_args)
        args.cwd = os.getcwd()
        args.stdout = io.StringIO()
        args.stderr = io.StringIO()

        d, fd_to_send = build_phase1_data(args)

        self.assertIn("message", d)
        self.assertNotIn("file", d)
        self.assertNotIn("directory", d)
        self.assertEqual(d["message"], message)
        self.assertEqual(fd_to_send, None)

    def test_file(self):
        filename = "my file"
        message = b"yay ponies\n"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        abs_filename = os.path.join(send_dir, filename)
        with open(abs_filename, "wb") as f:
            f.write(message)

        send_args = [ "send", filename ]
        args = runner.parser.parse_args(send_args)
        args.cwd = send_dir
        args.stdout = io.StringIO()
        args.stderr = io.StringIO()

        d, fd_to_send = build_phase1_data(args)

        self.assertNotIn("message", d)
        self.assertIn("file", d)
        self.assertNotIn("directory", d)
        self.assertEqual(d["file"]["filesize"], len(message))
        self.assertEqual(d["file"]["filename"], filename)
        self.assertEqual(fd_to_send.tell(), 0)
        self.assertEqual(fd_to_send.read(), message)

    def test_missing_file(self):
        filename = "missing"
        send_dir = self.mktemp()
        os.mkdir(send_dir)

        send_args = [ "send", filename ]
        args = runner.parser.parse_args(send_args)
        args.cwd = send_dir
        args.stdout = io.StringIO()
        args.stderr = io.StringIO()

        e = self.assertRaises(TransferError, build_phase1_data, args)
        self.assertEqual(str(e),
                         "Cannot send: no file/directory named '%s'" % filename)

    def _do_test_directory(self, addslash):
        parent_dir = self.mktemp()
        os.mkdir(parent_dir)
        send_dir = "dirname"
        os.mkdir(os.path.join(parent_dir, send_dir))
        ponies = [str(i) for i in range(5)]
        for p in ponies:
            with open(os.path.join(parent_dir, send_dir, p), "wb") as f:
                f.write(("%s ponies\n" % p).encode("ascii"))

        send_dir_arg = send_dir
        if addslash:
            send_dir_arg += os.sep
        send_args = [ "send", send_dir_arg ]
        args = runner.parser.parse_args(send_args)
        args.cwd = parent_dir
        args.stdout = io.StringIO()
        args.stderr = io.StringIO()

        d, fd_to_send = build_phase1_data(args)

        self.assertNotIn("message", d)
        self.assertNotIn("file", d)
        self.assertIn("directory", d)
        self.assertEqual(d["directory"]["dirname"], send_dir)
        self.assertEqual(d["directory"]["mode"], "zipfile/deflated")
        self.assertEqual(d["directory"]["numfiles"], 5)
        self.assertIn("numbytes", d["directory"])
        self.assertIsInstance(d["directory"]["numbytes"], six.integer_types)

        self.assertEqual(fd_to_send.tell(), 0)
        zdata = fd_to_send.read()
        self.assertEqual(len(zdata), d["directory"]["zipsize"])
        fd_to_send.seek(0, 0)
        with zipfile.ZipFile(fd_to_send, "r", zipfile.ZIP_DEFLATED) as zf:
            zipnames = zf.namelist()
            self.assertEqual(list(sorted(ponies)), list(sorted(zipnames)))
            for name in zipnames:
                contents = zf.open(name, "r").read()
                self.assertEqual(("%s ponies\n" % name).encode("ascii"),
                                 contents)

    def test_directory(self):
        return self._do_test_directory(addslash=False)

    def test_directory_addslash(self):
        return self._do_test_directory(addslash=True)

    def test_unknown(self):
        filename = "unknown"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        abs_filename = os.path.abspath(os.path.join(send_dir, filename))

        try:
            os.mkfifo(abs_filename)
        except AttributeError:
            raise unittest.SkipTest("is mkfifo supported on this platform?")

        # Delete the named pipe for the sake of users who might run "pip
        # wheel ." in this directory later. That command wants to copy
        # everything into a tempdir before building a wheel, and the
        # shutil.copy_tree() is uses can't handle the named pipe.
        self._things_to_delete.append(abs_filename)

        self.assertFalse(os.path.isfile(abs_filename))
        self.assertFalse(os.path.isdir(abs_filename))

        send_args = [ "send", filename ]
        args = runner.parser.parse_args(send_args)
        args.cwd = send_dir
        args.stdout = io.StringIO()
        args.stderr = io.StringIO()

        e = self.assertRaises(TypeError, build_phase1_data, args)
        self.assertEqual(str(e),
                         "'%s' is neither file nor directory" % filename)


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
            self.failUnlessEqual(ver, "magic-wormhole "+__version__+os.linesep)
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
    def _do_test(self, as_subprocess=False,
                 mode="text", addslash=False, override_filename=False):
        assert mode in ("text", "file", "directory")
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
            send_dirname_arg = os.path.join("middle", send_dirname)
            if addslash:
                send_dirname_arg += os.sep
            send_args.append(send_dirname_arg)
            receive_dirname = send_dirname

            receive_args.append("--accept-file")
            if override_filename:
                receive_args.extend(["-o", "outdir"])
                receive_dirname = "outdir"

        receive_args.append(code)

        if as_subprocess:
            wormhole_bin = self.find_executable()
            send_d = getProcessOutputAndValue(wormhole_bin, send_args,
                                              path=send_dir)
            receive_d = getProcessOutputAndValue(wormhole_bin, receive_args,
                                                 path=receive_dir)
            (send_res, receive_res) = yield gatherResults([send_d, receive_d],
                                                          True)
            send_stdout = send_res[0].decode("utf-8")
            send_stderr = send_res[1].decode("utf-8")
            send_rc = send_res[2]
            receive_stdout = receive_res[0].decode("utf-8")
            receive_stderr = receive_res[1].decode("utf-8")
            receive_rc = receive_res[2]
            NL = os.linesep
            self.assertEqual((send_rc, receive_rc), (0, 0),
                             (send_res, receive_res))
        else:
            sargs = runner.parser.parse_args(send_args)
            sargs.cwd = send_dir
            sargs.stdout = io.StringIO()
            sargs.stderr = io.StringIO()
            sargs.timing = DebugTiming()
            rargs = runner.parser.parse_args(receive_args)
            rargs.cwd = receive_dir
            rargs.stdout = io.StringIO()
            rargs.stderr = io.StringIO()
            rargs.timing = DebugTiming()
            send_d = cmd_send.send(sargs)
            receive_d = cmd_receive.receive(rargs)

            # The sender might fail, leaving the receiver hanging, or vice
            # versa. Make sure we don't wait on one side exclusively

            yield gatherResults([send_d, receive_d], True)
            send_stdout = sargs.stdout.getvalue()
            send_stderr = sargs.stderr.getvalue()
            receive_stdout = rargs.stdout.getvalue()
            receive_stderr = rargs.stderr.getvalue()

            # all output here comes from a StringIO, which uses \n for
            # newlines, even if we're on windows
            NL = "\n"

        self.maxDiff = None # show full output for assertion failures

        self.failUnlessEqual(send_stderr, "",
                             (send_stdout, send_stderr))
        self.failUnlessEqual(receive_stderr, "",
                             (receive_stdout, receive_stderr))

        # check sender
        if mode == "text":
            expected = ("Sending text message ({bytes:d} bytes){NL}"
                        "On the other computer, please run: "
                        "wormhole receive{NL}"
                        "Wormhole code is: {code}{NL}{NL}"
                        "text message sent{NL}").format(bytes=len(message),
                                                        code=code,
                                                        NL=NL)
            self.failUnlessEqual(send_stdout, expected)
        elif mode == "file":
            self.failUnlessIn("Sending {bytes:d} byte file named '{name}'{NL}"
                              .format(bytes=len(message), name=send_filename,
                                      NL=NL), send_stdout)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive{NL}"
                              "Wormhole code is: {code}{NL}{NL}"
                              .format(code=code, NL=NL),
                              send_stdout)
            self.failUnlessIn("File sent.. waiting for confirmation{NL}"
                              "Confirmation received. Transfer complete.{NL}"
                              .format(NL=NL), send_stdout)
        elif mode == "directory":
            self.failUnlessIn("Sending directory", send_stdout)
            self.failUnlessIn("named 'testdir'", send_stdout)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive{NL}"
                              "Wormhole code is: {code}{NL}{NL}"
                              .format(code=code, NL=NL), send_stdout)
            self.failUnlessIn("File sent.. waiting for confirmation{NL}"
                              "Confirmation received. Transfer complete.{NL}"
                              .format(NL=NL), send_stdout)

        # check receiver
        if mode == "text":
            self.failUnlessEqual(receive_stdout, message+NL)
        elif mode == "file":
            self.failUnlessIn("Receiving file ({bytes:d} bytes) into: {name}"
                              .format(bytes=len(message),
                                      name=receive_filename), receive_stdout)
            self.failUnlessIn("Received file written to ", receive_stdout)
            fn = os.path.join(receive_dir, receive_filename)
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), message)
        elif mode == "directory":
            want = (r"Receiving directory \(\d+ bytes\) into: {name}/"
                    .format(name=receive_dirname))
            self.failUnless(re.search(want, receive_stdout),
                            (want, receive_stdout))
            self.failUnlessIn("Received files written to {name}"
                              .format(name=receive_dirname), receive_stdout)
            fn = os.path.join(receive_dir, receive_dirname)
            self.failUnless(os.path.exists(fn), fn)
            for i in range(5):
                fn = os.path.join(receive_dir, receive_dirname, str(i))
                with open(fn, "r") as f:
                    self.failUnlessEqual(f.read(), message(i))

    def test_text(self):
        return self._do_test()
    def test_text_subprocess(self):
        return self._do_test(as_subprocess=True)

    def test_file(self):
        return self._do_test(mode="file")
    def test_file_override(self):
        return self._do_test(mode="file", override_filename=True)

    def test_directory(self):
        return self._do_test(mode="directory")
    def test_directory_addslash(self):
        return self._do_test(mode="directory", addslash=True)
    def test_directory_override(self):
        return self._do_test(mode="directory", override_filename=True)

class Cleanup(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_text(self):
        # the rendezvous channel should be deleted after success
        code = u"1-abc"
        common_args = ["--hide-progress",
                       "--relay-url", self.relayurl,
                       "--transit-helper", ""]
        sargs = runner.parser.parse_args(common_args +
                                         ["send",
                                          "--text", "secret message",
                                          "--code", code])
        sargs.stdout = io.StringIO()
        sargs.stderr = io.StringIO()
        sargs.timing = DebugTiming()
        rargs = runner.parser.parse_args(common_args +
                                         ["receive", code])
        rargs.stdout = io.StringIO()
        rargs.stderr = io.StringIO()
        rargs.timing = DebugTiming()
        send_d = cmd_send.send(sargs)
        receive_d = cmd_receive.receive(rargs)

        yield send_d
        yield receive_d

        cids = self._rendezvous.get_app(cmd_send.APPID).get_allocated()
        self.assertEqual(len(cids), 0)

    @inlineCallbacks
    def test_text_wrong_password(self):
        # if the password was wrong, the rendezvous channel should still be
        # deleted
        common_args = ["--hide-progress",
                       "--relay-url", self.relayurl,
                       "--transit-helper", ""]
        sargs = runner.parser.parse_args(common_args +
                                         ["send",
                                          "--text", "secret message",
                                          "--code", u"1-abc"])
        sargs.stdout = io.StringIO()
        sargs.stderr = io.StringIO()
        sargs.timing = DebugTiming()
        rargs = runner.parser.parse_args(common_args +
                                         ["receive", u"1-WRONG"])
        rargs.stdout = io.StringIO()
        rargs.stderr = io.StringIO()
        rargs.timing = DebugTiming()
        send_d = cmd_send.send(sargs)
        receive_d = cmd_receive.receive(rargs)

        # both sides should be capable of detecting the mismatch
        yield self.assertFailure(send_d, WrongPasswordError)
        yield self.assertFailure(receive_d, WrongPasswordError)

        cids = self._rendezvous.get_app(cmd_send.APPID).get_allocated()
        self.assertEqual(len(cids), 0)

