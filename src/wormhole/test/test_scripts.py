from __future__ import print_function, unicode_literals
import os, sys, re, io, zipfile, six, stat
import mock
from twisted.trial import unittest
from twisted.python import procutils, log
from twisted.internet.utils import getProcessOutputAndValue
from twisted.internet.defer import gatherResults, inlineCallbacks
from .. import __version__
from .common import ServerBase
from ..cli import cmd_send, cmd_receive
from ..cli.cli import Config
from ..errors import TransferError, WrongPasswordError, WelcomeError


def build_offer(args):
    s = cmd_send.Sender(args, None)
    return s._build_offer()


class OfferData(unittest.TestCase):
    def setUp(self):
        self._things_to_delete = []
        self.cfg = cfg = Config()
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    def tearDown(self):
        for fn in self._things_to_delete:
            if os.path.exists(fn):
                os.unlink(fn)
        del self.cfg

    def test_text(self):
        self.cfg.text = message = "blah blah blah ponies"
        d, fd_to_send = build_offer(self.cfg)

        self.assertIn("message", d)
        self.assertNotIn("file", d)
        self.assertNotIn("directory", d)
        self.assertEqual(d["message"], message)
        self.assertEqual(fd_to_send, None)

    def test_file(self):
        self.cfg.what = filename = "my file"
        message = b"yay ponies\n"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        abs_filename = os.path.join(send_dir, filename)
        with open(abs_filename, "wb") as f:
            f.write(message)

        self.cfg.cwd = send_dir
        d, fd_to_send = build_offer(self.cfg)

        self.assertNotIn("message", d)
        self.assertIn("file", d)
        self.assertNotIn("directory", d)
        self.assertEqual(d["file"]["filesize"], len(message))
        self.assertEqual(d["file"]["filename"], filename)
        self.assertEqual(fd_to_send.tell(), 0)
        self.assertEqual(fd_to_send.read(), message)

    def test_missing_file(self):
        self.cfg.what = filename = "missing"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        self.cfg.cwd = send_dir

        e = self.assertRaises(TransferError, build_offer, self.cfg)
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
        self.cfg.what = send_dir_arg
        self.cfg.cwd = parent_dir

        d, fd_to_send = build_offer(self.cfg)

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
        self.cfg.what = filename = "unknown"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        abs_filename = os.path.abspath(os.path.join(send_dir, filename))
        self.cfg.cwd = send_dir

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

        e = self.assertRaises(TypeError, build_offer, self.cfg)
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

    @inlineCallbacks
    def test_version(self):
        # "wormhole" must be on the path, so e.g. "pip install -e ." in a
        # virtualenv. This guards against an environment where the tests
        # below might run the wrong executable.
        self.maxDiff = None
        wormhole = self.find_executable()
        # we must pass on the environment so that "something" doesn't
        # get sad about UTF8 vs. ascii encodings
        out, err, rc = yield getProcessOutputAndValue(wormhole, ["--version"], env=os.environ)
        err = err.decode("utf-8")
        if "DistributionNotFound" in err:
            log.msg("stderr was %s" % err)
            last = err.strip().split("\n")[-1]
            self.fail("wormhole not runnable: %s" % last)
        ver = out.decode("utf-8") or err
        self.failUnlessEqual(ver.strip(), "magic-wormhole {}".format(__version__))
        self.failUnlessEqual(rc, 0)

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
        send_cfg = Config()
        recv_cfg = Config()
        message = "blah blah blah ponies"

        for cfg in [send_cfg, recv_cfg]:
            cfg.hide_progress = True
            cfg.relay_url = self.relayurl
            cfg.transit_helper = ""
            cfg.listen = True
            cfg.code = "1-abc"
            cfg.stdout = io.StringIO()
            cfg.stderr = io.StringIO()

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        receive_dir = self.mktemp()
        os.mkdir(receive_dir)

        if mode == "text":
            send_cfg.text = message

        elif mode == "file":
            send_filename = "testfile"
            with open(os.path.join(send_dir, send_filename), "w") as f:
                f.write(message)
            send_cfg.what = send_filename
            receive_filename = send_filename

            recv_cfg.accept_file = True
            if override_filename:
                recv_cfg.output_file = receive_filename = "outfile"

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
            modes = {}
            for i in range(5):
                path = os.path.join(source_dir, str(i))
                with open(path, "w") as f:
                    f.write(message(i))
                if i == 3:
                    os.chmod(path, 0o755)
                modes[i] = stat.S_IMODE(os.stat(path).st_mode)
            send_dirname_arg = os.path.join("middle", send_dirname)
            if addslash:
                send_dirname_arg += os.sep
            send_cfg.what = send_dirname_arg
            receive_dirname = send_dirname

            recv_cfg.accept_file = True
            if override_filename:
                recv_cfg.output_file = receive_dirname = "outdir"

        if as_subprocess:
            wormhole_bin = self.find_executable()
            if send_cfg.text:
                content_args = ['--text', send_cfg.text]
            elif send_cfg.what:
                content_args = [send_cfg.what]

            send_args = [
                    '--hide-progress',
                    '--relay-url', self.relayurl,
                    '--transit-helper', '',
                    'send',
                    '--code', send_cfg.code,
                ] + content_args

            send_d = getProcessOutputAndValue(
                wormhole_bin, send_args,
                path=send_dir,
            )
            recv_args = [
                '--hide-progress',
                '--relay-url', self.relayurl,
                '--transit-helper', '',
                'receive',
                '--accept-file',
                recv_cfg.code,
            ]
            if override_filename:
                recv_args.extend(['-o', receive_filename])

            receive_d = getProcessOutputAndValue(
                wormhole_bin, recv_args,
                path=receive_dir,
            )

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
            send_cfg.cwd = send_dir
            send_d = cmd_send.send(send_cfg)

            recv_cfg.cwd = receive_dir
            receive_d = cmd_receive.receive(recv_cfg)

            # The sender might fail, leaving the receiver hanging, or vice
            # versa. Make sure we don't wait on one side exclusively

            yield gatherResults([send_d, receive_d], True)
            # XXX need captured stdin/stdout from sender/receiver
            send_stdout = send_cfg.stdout.getvalue()
            send_stderr = send_cfg.stderr.getvalue()
            receive_stdout = recv_cfg.stdout.getvalue()
            receive_stderr = recv_cfg.stderr.getvalue()

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
                                                        code=send_cfg.code,
                                                        NL=NL)
            self.failUnlessEqual(send_stdout, expected)
        elif mode == "file":
            self.failUnlessIn("Sending {bytes:d} byte file named '{name}'{NL}"
                              .format(bytes=len(message), name=send_filename,
                                      NL=NL), send_stdout)
            self.failUnlessIn("On the other computer, please run: "
                              "wormhole receive{NL}"
                              "Wormhole code is: {code}{NL}{NL}"
                              .format(code=send_cfg.code, NL=NL),
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
                              .format(code=send_cfg.code, NL=NL), send_stdout)
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
                self.failUnlessEqual(modes[i],
                                     stat.S_IMODE(os.stat(fn).st_mode))

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

    @inlineCallbacks
    def test_file_noclobber(self):
        send_cfg = Config()
        recv_cfg = Config()

        for cfg in [send_cfg, recv_cfg]:
            cfg.hide_progress = True
            cfg.relay_url = self.relayurl
            cfg.transit_helper = ""
            cfg.listen = False
            cfg.code = code = "1-abc"
            cfg.stdout = io.StringIO()
            cfg.stderr = io.StringIO()

        message = "test message"

        recv_cfg.accept_file = True

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        receive_dir = self.mktemp()
        os.mkdir(receive_dir)

        send_filename = "testfile"
        with open(os.path.join(send_dir, send_filename), "w") as f:
            f.write(message)
        send_cfg.what = receive_filename = send_filename
        recv_cfg.what = receive_filename

        PRESERVE = "don't clobber me\n"
        clobberable = os.path.join(receive_dir, receive_filename)
        with open(clobberable, "w") as f:
            f.write(PRESERVE)

        send_cfg.cwd = send_dir
        send_d = cmd_send.send(send_cfg)

        recv_cfg.cwd = receive_dir
        receive_d = cmd_receive.receive(recv_cfg)

        # both sides will fail because of the pre-existing file

        f = yield self.assertFailure(send_d, TransferError)
        self.assertEqual(str(f), "remote error, transfer abandoned: file already exists")

        f = yield self.assertFailure(receive_d, TransferError)
        self.assertEqual(str(f), "file already exists")

        send_stdout = send_cfg.stdout.getvalue()
        send_stderr = send_cfg.stderr.getvalue()
        receive_stdout = recv_cfg.stdout.getvalue()
        receive_stderr = recv_cfg.stderr.getvalue()

        # all output here comes from a StringIO, which uses \n for
        # newlines, even if we're on windows
        NL = "\n"

        self.maxDiff = None # show full output for assertion failures

        self.failUnlessEqual(send_stderr, "",
                             (send_stdout, send_stderr))
        self.failUnlessEqual(receive_stderr, "",
                             (receive_stdout, receive_stderr))

        # check sender
        self.failUnlessIn("Sending {bytes:d} byte file named '{name}'{NL}"
                          .format(bytes=len(message), name=send_filename,
                                  NL=NL), send_stdout)
        self.failUnlessIn("On the other computer, please run: "
                          "wormhole receive{NL}"
                          "Wormhole code is: {code}{NL}{NL}"
                          .format(code=code, NL=NL),
                          send_stdout)
        self.failIfIn("File sent.. waiting for confirmation{NL}"
                      "Confirmation received. Transfer complete.{NL}"
                      .format(NL=NL), send_stdout)

        # check receiver
        self.failUnlessIn("Error: "
                          "refusing to overwrite existing file testfile{NL}"
                          .format(NL=NL), receive_stdout)
        self.failIfIn("Received file written to ", receive_stdout)
        fn = os.path.join(receive_dir, receive_filename)
        self.failUnless(os.path.exists(fn))
        with open(fn, "r") as f:
            self.failUnlessEqual(f.read(), PRESERVE)

class NotWelcome(ServerBase, unittest.TestCase):
    def setUp(self):
        self._setup_relay(error="please upgrade XYZ")
        self.cfg = cfg = Config()
        cfg.hide_progress = True
        cfg.listen = False
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    @inlineCallbacks
    def test_sender(self):
        self.cfg.text = "hi"
        self.cfg.code = "1-abc"

        send_d = cmd_send.send(self.cfg)
        f = yield self.assertFailure(send_d, WelcomeError)
        self.assertEqual(str(f), "please upgrade XYZ")

    @inlineCallbacks
    def test_receiver(self):
        self.cfg.code = "1-abc"

        receive_d = cmd_receive.receive(self.cfg)
        f = yield self.assertFailure(receive_d, WelcomeError)
        self.assertEqual(str(f), "please upgrade XYZ")


class Cleanup(ServerBase, unittest.TestCase):

    def setUp(self):
        d = super(Cleanup, self).setUp()
        self.cfg = cfg = Config()
        # common options for all tests in this suite
        cfg.hide_progress = True
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()
        return d

    @inlineCallbacks
    @mock.patch('sys.stdout')
    def test_text(self, stdout):
        # the rendezvous channel should be deleted after success
        self.cfg.text = "hello"
        self.cfg.code = "1-abc"

        send_d = cmd_send.send(self.cfg)
        receive_d = cmd_receive.receive(self.cfg)

        # XXX DeferredList?
        yield send_d
        yield receive_d

        cids = self._rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        self.assertEqual(len(cids), 0)

    @inlineCallbacks
    def test_text_wrong_password(self):
        # if the password was wrong, the rendezvous channel should still be
        # deleted
        self.cfg.text = "secret message"
        self.cfg.code = "1-abc"
        send_d = cmd_send.send(self.cfg)

        self.cfg.code = "1-WRONG"
        receive_d = cmd_receive.receive(self.cfg)

        # both sides should be capable of detecting the mismatch
        yield self.assertFailure(send_d, WrongPasswordError)
        yield self.assertFailure(receive_d, WrongPasswordError)

        cids = self._rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        self.assertEqual(len(cids), 0)
        self.flushLoggedErrors(WrongPasswordError)

class ExtractFile(unittest.TestCase):
    def test_filenames(self):
        args = mock.Mock()
        args.relay_url = ""
        ef = cmd_receive.TwistedReceiver(args)._extract_file
        extract_dir = os.path.abspath(self.mktemp())

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "ok"
        zi.external_attr = 5 << 16
        expected = os.path.join(extract_dir, "ok")
        with mock.patch.object(cmd_receive.os, "chmod") as chmod:
            ef(zf, zi, extract_dir)
            self.assertEqual(zf.extract.mock_calls,
                             [mock.call(zi.filename, path=extract_dir)])
            self.assertEqual(chmod.mock_calls, [mock.call(expected, 5)])

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "../haha"
        e = self.assertRaises(ValueError, ef, zf, zi, extract_dir)
        self.assertIn("malicious zipfile", str(e))

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "haha//root" # abspath squashes this, hopefully zipfile
                                   # does too
        zi.external_attr = 5 << 16
        expected = os.path.join(extract_dir, "haha", "root")
        with mock.patch.object(cmd_receive.os, "chmod") as chmod:
            ef(zf, zi, extract_dir)
            self.assertEqual(zf.extract.mock_calls,
                             [mock.call(zi.filename, path=extract_dir)])
            self.assertEqual(chmod.mock_calls, [mock.call(expected, 5)])

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "/etc/passwd"
        e = self.assertRaises(ValueError, ef, zf, zi, extract_dir)
        self.assertIn("malicious zipfile", str(e))
