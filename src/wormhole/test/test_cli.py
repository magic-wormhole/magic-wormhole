from __future__ import print_function

import io
import os
import re
import stat
import sys
import zipfile
from textwrap import dedent, fill

import six
from click.testing import CliRunner
from humanize import naturalsize
from twisted.internet import endpoints, reactor
from twisted.internet.defer import gatherResults, inlineCallbacks, returnValue
from twisted.internet.error import ConnectionRefusedError
from twisted.internet.utils import getProcessOutputAndValue
from twisted.python import log, procutils
from twisted.trial import unittest
from zope.interface import implementer

import mock

from .. import __version__
from .._interfaces import ITorManager
from ..cli import cli, cmd_receive, cmd_send, welcome
from ..errors import (ServerConnectionError, TransferError,
                      UnsendableFileError, WelcomeError, WrongPasswordError)
from .common import ServerBase, config


def build_offer(args):
    s = cmd_send.Sender(args, None)
    return s._build_offer()


class OfferData(unittest.TestCase):
    def setUp(self):
        self._things_to_delete = []
        self.cfg = cfg = config("send")
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

    def _create_broken_symlink(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("host OS does not support symlinks")

        parent_dir = self.mktemp()
        os.mkdir(parent_dir)
        send_dir = "dirname"
        os.mkdir(os.path.join(parent_dir, send_dir))
        os.symlink('/non/existent/file',
                   os.path.join(parent_dir, send_dir, 'linky'))

        send_dir_arg = send_dir
        self.cfg.what = send_dir_arg
        self.cfg.cwd = parent_dir

    def test_broken_symlink_raises_err(self):
        self._create_broken_symlink()
        self.cfg.ignore_unsendable_files = False
        e = self.assertRaises(UnsendableFileError, build_offer, self.cfg)

        # On english distributions of Linux, this will be
        # "linky: No such file or directory", but the error may be
        # different on Windows and other locales and/or Unix variants, so
        # we'll just assert the part we know about.
        self.assertIn("linky: ", str(e))

    def test_broken_symlink_is_ignored(self):
        self._create_broken_symlink()
        self.cfg.ignore_unsendable_files = True
        d, fd_to_send = build_offer(self.cfg)
        self.assertIn('(ignoring error)', self.cfg.stderr.getvalue())
        self.assertEqual(d['directory']['numfiles'], 0)
        self.assertEqual(d['directory']['numbytes'], 0)

    def test_missing_file(self):
        self.cfg.what = filename = "missing"
        send_dir = self.mktemp()
        os.mkdir(send_dir)
        self.cfg.cwd = send_dir

        e = self.assertRaises(TransferError, build_offer, self.cfg)
        self.assertEqual(
            str(e), "Cannot send: no file/directory named '%s'" % filename)

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
        self.assertEqual(
            str(e), "'%s' is neither file nor directory" % filename)

    def test_symlink(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("host OS does not support symlinks")
        # build A/B1 -> B2 (==A/B2), and A/B2/C.txt
        parent_dir = self.mktemp()
        os.mkdir(parent_dir)
        os.mkdir(os.path.join(parent_dir, "B2"))
        with open(os.path.join(parent_dir, "B2", "C.txt"), "wb") as f:
            f.write(b"success")
        os.symlink("B2", os.path.join(parent_dir, "B1"))
        # now send "B1/C.txt" from A, and it should get the right file
        self.cfg.cwd = parent_dir
        self.cfg.what = os.path.join("B1", "C.txt")
        d, fd_to_send = build_offer(self.cfg)
        self.assertEqual(d["file"]["filename"], "C.txt")
        self.assertEqual(fd_to_send.read(), b"success")

    def test_symlink_collapse(self):
        if not hasattr(os, 'symlink'):
            raise unittest.SkipTest("host OS does not support symlinks")
        # build A/B1, A/B1/D.txt
        # A/B2/C2, A/B2/D.txt
        # symlink A/B1/C1 -> A/B2/C2
        parent_dir = self.mktemp()
        os.mkdir(parent_dir)
        os.mkdir(os.path.join(parent_dir, "B1"))
        with open(os.path.join(parent_dir, "B1", "D.txt"), "wb") as f:
            f.write(b"fail")
        os.mkdir(os.path.join(parent_dir, "B2"))
        os.mkdir(os.path.join(parent_dir, "B2", "C2"))
        with open(os.path.join(parent_dir, "B2", "D.txt"), "wb") as f:
            f.write(b"success")
        os.symlink(
            os.path.abspath(os.path.join(parent_dir, "B2", "C2")),
            os.path.join(parent_dir, "B1", "C1"))
        # Now send "B1/C1/../D.txt" from A. The correct traversal will be:
        # * start: A
        # * B1: A/B1
        # * C1: follow symlink to A/B2/C2
        # * ..: climb to A/B2
        # * D.txt: open A/B2/D.txt, which contains "success"
        # If the code mistakenly uses normpath(), it would do:
        # * normpath turns B1/C1/../D.txt into B1/D.txt
        # * start: A
        # * B1: A/B1
        # * D.txt: open A/B1/D.txt , which contains "fail"
        self.cfg.cwd = parent_dir
        self.cfg.what = os.path.join("B1", "C1", os.pardir, "D.txt")
        d, fd_to_send = build_offer(self.cfg)
        self.assertEqual(d["file"]["filename"], "D.txt")
        self.assertEqual(fd_to_send.read(), b"success")

    if os.name == "nt":
        test_symlink_collapse.todo = "host OS has broken os.path.realpath()"
        # ntpath.py's realpath() is built out of normpath(), and does not
        # follow symlinks properly, so this test always fails. "wormhole send
        # PATH" on windows will do the wrong thing. See
        # https://bugs.python.org/issue9949" for details. I'm making this a
        # TODO instead of a SKIP because 1: this causes an observable
        # misbehavior (albeit in rare circumstances), 2: it probably used to
        # work (sometimes, but not in #251). See cmd_send.py for more notes.


class LocaleFinder:
    def __init__(self):
        self._run_once = False

    @inlineCallbacks
    def find_utf8_locale(self):
        if sys.platform == "win32":
            returnValue("en_US.UTF-8")
        if self._run_once:
            returnValue(self._best_locale)
        self._best_locale = yield self._find_utf8_locale()
        self._run_once = True
        returnValue(self._best_locale)

    @inlineCallbacks
    def _find_utf8_locale(self):
        # Click really wants to be running under a unicode-capable locale,
        # especially on python3. macOS has en-US.UTF-8 but not C.UTF-8, and
        # most linux boxes have C.UTF-8 but not en-US.UTF-8 . For tests,
        # figure out which one is present and use that. For runtime, it's a
        # mess, as really the user must take responsibility for setting their
        # locale properly. I'm thinking of abandoning Click and going back to
        # twisted.python.usage to avoid this problem in the future.
        (out, err, rc) = yield getProcessOutputAndValue("locale", ["-a"])
        if rc != 0:
            log.msg("error running 'locale -a', rc=%s" % (rc, ))
            log.msg("stderr: %s" % (err, ))
            returnValue(None)
        out = out.decode("utf-8")  # make sure we get a string
        utf8_locales = {}
        for locale in out.splitlines():
            locale = locale.strip()
            if locale.lower().endswith((".utf-8", ".utf8")):
                utf8_locales[locale.lower()] = locale
        for wanted in ["C.utf8", "C.UTF-8", "en_US.utf8", "en_US.UTF-8"]:
            if wanted.lower() in utf8_locales:
                returnValue(utf8_locales[wanted.lower()])
        if utf8_locales:
            returnValue(list(utf8_locales.values())[0])
        returnValue(None)


locale_finder = LocaleFinder()


class ScriptsBase:
    def find_executable(self):
        # to make sure we're running the right executable (in a virtualenv),
        # we require that our "wormhole" lives in the same directory as our
        # "python"
        locations = procutils.which("wormhole")
        if not locations:
            raise unittest.SkipTest("unable to find 'wormhole' in $PATH")
        wormhole = locations[0]
        if (os.path.dirname(os.path.abspath(wormhole)) != os.path.dirname(
                sys.executable)):
            log.msg("locations: %s" % (locations, ))
            log.msg("sys.executable: %s" % (sys.executable, ))
            raise unittest.SkipTest(
                "found the wrong 'wormhole' in $PATH: %s %s" %
                (wormhole, sys.executable))
        return wormhole

    @inlineCallbacks
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

        # Setting LANG/LC_ALL to a unicode-capable locale is necessary to
        # convince Click to not complain about a forced-ascii locale. My
        # apologies to folks who want to run tests on a machine that doesn't
        # have the C.UTF-8 locale installed.
        locale = yield locale_finder.find_utf8_locale()
        if not locale:
            raise unittest.SkipTest("unable to find UTF-8 locale")
        locale_env = dict(LC_ALL=locale, LANG=locale)
        wormhole = self.find_executable()
        res = yield getProcessOutputAndValue(
            wormhole, ["--version"], env=locale_env)
        out, err, rc = res
        if rc != 0:
            log.msg("wormhole not runnable in this tree:")
            log.msg("out", out)
            log.msg("err", err)
            log.msg("rc", rc)
            raise unittest.SkipTest("wormhole is not runnable in this tree")
        returnValue(locale_env)


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
        out, err, rc = yield getProcessOutputAndValue(
            wormhole, ["--version"], env=os.environ)
        err = err.decode("utf-8")
        if "DistributionNotFound" in err:
            log.msg("stderr was %s" % err)
            last = err.strip().split("\n")[-1]
            self.fail("wormhole not runnable: %s" % last)
        ver = out.decode("utf-8") or err
        self.failUnlessEqual(ver.strip(),
                             "magic-wormhole {}".format(__version__))
        self.failUnlessEqual(rc, 0)


@implementer(ITorManager)
class FakeTor:
    # use normal endpoints, but record the fact that we were asked
    def __init__(self):
        self.endpoints = []

    def stream_via(self, host, port):
        self.endpoints.append((host, port))
        return endpoints.HostnameEndpoint(reactor, host, port)


class PregeneratedCode(ServerBase, ScriptsBase, unittest.TestCase):
    # we need Twisted to run the server, but we run the sender and receiver
    # with deferToThread()

    @inlineCallbacks
    def setUp(self):
        self._env = yield self.is_runnable()
        yield ServerBase.setUp(self)

    @inlineCallbacks
    def _do_test(self,
                 as_subprocess=False,
                 mode="text",
                 addslash=False,
                 override_filename=False,
                 fake_tor=False,
                 overwrite=False,
                 mock_accept=False):
        assert mode in ("text", "file", "empty-file", "directory", "slow-text",
                        "slow-sender-text")
        if fake_tor:
            assert not as_subprocess
        send_cfg = config("send")
        recv_cfg = config("receive")
        message = "blah blah blah ponies"

        for cfg in [send_cfg, recv_cfg]:
            cfg.hide_progress = True
            cfg.relay_url = self.relayurl
            cfg.transit_helper = ""
            cfg.listen = True
            cfg.code = u"1-abc"
            cfg.stdout = io.StringIO()
            cfg.stderr = io.StringIO()

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        receive_dir = self.mktemp()
        os.mkdir(receive_dir)

        if mode in ("text", "slow-text", "slow-sender-text"):
            send_cfg.text = message

        elif mode in ("file", "empty-file"):
            if mode == "empty-file":
                message = ""
            send_filename = u"testfil\u00EB"  # e-with-diaeresis
            with open(os.path.join(send_dir, send_filename), "w") as f:
                f.write(message)
            send_cfg.what = send_filename
            receive_filename = send_filename

            recv_cfg.accept_file = False if mock_accept else True
            if override_filename:
                recv_cfg.output_file = receive_filename = u"outfile"
            if overwrite:
                recv_cfg.output_file = receive_filename
                existing_file = os.path.join(receive_dir, receive_filename)
                with open(existing_file, 'w') as f:
                    f.write('pls overwrite me')

        elif mode == "directory":
            # $send_dir/
            # $send_dir/middle/
            # $send_dir/middle/$dirname/
            # $send_dir/middle/$dirname/[12345]
            # cd $send_dir && wormhole send middle/$dirname
            # cd $receive_dir && wormhole receive
            # expect: $receive_dir/$dirname/[12345]

            send_dirname = u"testdir"

            def message(i):
                return "test message %d\n" % i

            os.mkdir(os.path.join(send_dir, u"middle"))
            source_dir = os.path.join(send_dir, u"middle", send_dirname)
            os.mkdir(source_dir)
            modes = {}
            for i in range(5):
                path = os.path.join(source_dir, str(i))
                with open(path, "w") as f:
                    f.write(message(i))
                if i == 3:
                    os.chmod(path, 0o755)
                modes[i] = stat.S_IMODE(os.stat(path).st_mode)
            send_dirname_arg = os.path.join(u"middle", send_dirname)
            if addslash:
                send_dirname_arg += os.sep
            send_cfg.what = send_dirname_arg
            receive_dirname = send_dirname

            recv_cfg.accept_file = False if mock_accept else True
            if override_filename:
                recv_cfg.output_file = receive_dirname = u"outdir"
            if overwrite:
                recv_cfg.output_file = receive_dirname
                os.mkdir(os.path.join(receive_dir, receive_dirname))

        if as_subprocess:
            wormhole_bin = self.find_executable()
            if send_cfg.text:
                content_args = ['--text', send_cfg.text]
            elif send_cfg.what:
                content_args = [send_cfg.what]

            # raise the rx KEY_TIMER to some large number here, to avoid
            # spurious test failures on hosts that are slow enough to trigger
            # the "Waiting for sender..." pacifier message. We can do in
            # not-as_subprocess, because we can directly patch the value before
            # running the receiver. But we can't patch across the subprocess
            # boundary, so we use an environment variable.
            env = self._env.copy()
            env["_MAGIC_WORMHOLE_TEST_KEY_TIMER"] = "999999"
            env["_MAGIC_WORMHOLE_TEST_VERIFY_TIMER"] = "999999"
            send_args = [
                '--relay-url',
                self.relayurl,
                '--transit-helper',
                '',
                'send',
                '--hide-progress',
                '--code',
                send_cfg.code,
            ] + content_args

            send_d = getProcessOutputAndValue(
                wormhole_bin,
                send_args,
                path=send_dir,
                env=env,
            )
            recv_args = [
                '--relay-url',
                self.relayurl,
                '--transit-helper',
                '',
                'receive',
                '--hide-progress',
                '--accept-file',
                recv_cfg.code,
            ]
            if override_filename:
                recv_args.extend(['-o', receive_filename])

            receive_d = getProcessOutputAndValue(
                wormhole_bin,
                recv_args,
                path=receive_dir,
                env=env,
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
            recv_cfg.cwd = receive_dir

            if fake_tor:
                send_cfg.tor = True
                send_cfg.transit_helper = self.transit
                tx_tm = FakeTor()
                with mock.patch(
                        "wormhole.tor_manager.get_tor",
                        return_value=tx_tm,
                ) as mtx_tm:
                    send_d = cmd_send.send(send_cfg)

                recv_cfg.tor = True
                recv_cfg.transit_helper = self.transit
                rx_tm = FakeTor()
                with mock.patch(
                        "wormhole.tor_manager.get_tor",
                        return_value=rx_tm,
                ) as mrx_tm:
                    receive_d = cmd_receive.receive(recv_cfg)
            else:
                KEY_TIMER = 0 if mode == "slow-sender-text" else 99999
                rxw = []
                with mock.patch.object(cmd_receive, "KEY_TIMER", KEY_TIMER):
                    send_d = cmd_send.send(send_cfg)
                    receive_d = cmd_receive.receive(
                        recv_cfg, _debug_stash_wormhole=rxw)
                    # we need to keep KEY_TIMER patched until the receiver
                    # gets far enough to start the timer, which happens after
                    # the code is set
                    if mode == "slow-sender-text":
                        yield rxw[0].get_unverified_key()

            # The sender might fail, leaving the receiver hanging, or vice
            # versa. Make sure we don't wait on one side exclusively
            VERIFY_TIMER = 0 if mode == "slow-text" else 99999
            with mock.patch.object(cmd_receive, "VERIFY_TIMER", VERIFY_TIMER):
                with mock.patch.object(cmd_send, "VERIFY_TIMER", VERIFY_TIMER):
                    if mock_accept:
                        with mock.patch.object(
                                cmd_receive.six.moves, 'input',
                                return_value='y'):
                            yield gatherResults([send_d, receive_d], True)
                    else:
                        yield gatherResults([send_d, receive_d], True)

            if fake_tor:
                expected_endpoints = [("127.0.0.1", self.rdv_ws_port)]
                if mode in ("file", "directory"):
                    expected_endpoints.append(("127.0.0.1", self.transitport))
                tx_timing = mtx_tm.call_args[1]["timing"]
                self.assertEqual(tx_tm.endpoints, expected_endpoints)
                self.assertEqual(
                    mtx_tm.mock_calls,
                    [mock.call(reactor, False, None, timing=tx_timing)])
                rx_timing = mrx_tm.call_args[1]["timing"]
                self.assertEqual(rx_tm.endpoints, expected_endpoints)
                self.assertEqual(
                    mrx_tm.mock_calls,
                    [mock.call(reactor, False, None, timing=rx_timing)])

            send_stdout = send_cfg.stdout.getvalue()
            send_stderr = send_cfg.stderr.getvalue()
            receive_stdout = recv_cfg.stdout.getvalue()
            receive_stderr = recv_cfg.stderr.getvalue()

            # all output here comes from a StringIO, which uses \n for
            # newlines, even if we're on windows
            NL = "\n"

        self.maxDiff = None  # show full output for assertion failures

        key_established = ""
        if mode == "slow-text":
            key_established = "Key established, waiting for confirmation...\n"

        self.assertEqual(send_stdout, "")

        # check sender
        if mode == "text" or mode == "slow-text":
            expected = ("Sending text message ({bytes:d} Bytes){NL}"
                        "Wormhole code is: {code}{NL}"
                        "On the other computer, please run:{NL}{NL}"
                        "wormhole receive {code}{NL}{NL}"
                        "{KE}"
                        "text message sent{NL}").format(
                            bytes=len(message),
                            code=send_cfg.code,
                            NL=NL,
                            KE=key_established)
            self.failUnlessEqual(send_stderr, expected)
        elif mode == "file":
            self.failUnlessIn(u"Sending {size:s} file named '{name}'{NL}"
                              .format(
                                  size=naturalsize(len(message)),
                                  name=send_filename,
                                  NL=NL), send_stderr)
            self.failUnlessIn(u"Wormhole code is: {code}{NL}"
                              "On the other computer, please run:{NL}{NL}"
                              "wormhole receive {code}{NL}{NL}".format(
                                  code=send_cfg.code, NL=NL), send_stderr)
            self.failUnlessIn(
                u"File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}".format(NL=NL),
                send_stderr)
        elif mode == "directory":
            self.failUnlessIn(u"Sending directory", send_stderr)
            self.failUnlessIn(u"named 'testdir'", send_stderr)
            self.failUnlessIn(u"Wormhole code is: {code}{NL}"
                              "On the other computer, please run:{NL}{NL}"
                              "wormhole receive {code}{NL}{NL}".format(
                                  code=send_cfg.code, NL=NL), send_stderr)
            self.failUnlessIn(
                u"File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}".format(NL=NL),
                send_stderr)

        # check receiver
        if mode in ("text", "slow-text", "slow-sender-text"):
            self.assertEqual(receive_stdout, message + NL)
            if mode == "text":
                self.assertEqual(receive_stderr, "")
            elif mode == "slow-text":
                self.assertEqual(receive_stderr, key_established)
            elif mode == "slow-sender-text":
                self.assertEqual(receive_stderr, "Waiting for sender...\n")
        elif mode == "file":
            self.failUnlessEqual(receive_stdout, "")
            self.failUnlessIn(u"Receiving file ({size:s}) into: {name}".format(
                size=naturalsize(len(message)), name=receive_filename),
                receive_stderr)
            self.failUnlessIn(u"Received file written to ", receive_stderr)
            fn = os.path.join(receive_dir, receive_filename)
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), message)
        elif mode == "directory":
            self.failUnlessEqual(receive_stdout, "")
            want = (r"Receiving directory \(\d+ \w+\) into: {name}/"
                    .format(name=receive_dirname))
            self.failUnless(
                re.search(want, receive_stderr), (want, receive_stderr))
            self.failUnlessIn(
                u"Received files written to {name}"
                .format(name=receive_dirname),
                receive_stderr)
            fn = os.path.join(receive_dir, receive_dirname)
            self.failUnless(os.path.exists(fn), fn)
            for i in range(5):
                fn = os.path.join(receive_dir, receive_dirname, str(i))
                with open(fn, "r") as f:
                    self.failUnlessEqual(f.read(), message(i))
                self.failUnlessEqual(modes[i], stat.S_IMODE(
                    os.stat(fn).st_mode))

    def test_text(self):
        return self._do_test()

    def test_text_subprocess(self):
        return self._do_test(as_subprocess=True)

    def test_text_tor(self):
        return self._do_test(fake_tor=True)

    def test_file(self):
        return self._do_test(mode="file")

    def test_file_override(self):
        return self._do_test(mode="file", override_filename=True)

    def test_file_overwrite(self):
        return self._do_test(mode="file", overwrite=True)

    def test_file_overwrite_mock_accept(self):
        return self._do_test(mode="file", overwrite=True, mock_accept=True)

    def test_file_tor(self):
        return self._do_test(mode="file", fake_tor=True)

    def test_empty_file(self):
        return self._do_test(mode="empty-file")

    def test_directory(self):
        return self._do_test(mode="directory")

    def test_directory_addslash(self):
        return self._do_test(mode="directory", addslash=True)

    def test_directory_override(self):
        return self._do_test(mode="directory", override_filename=True)

    def test_directory_overwrite(self):
        return self._do_test(mode="directory", overwrite=True)

    def test_directory_overwrite_mock_accept(self):
        return self._do_test(
            mode="directory", overwrite=True, mock_accept=True)

    def test_slow_text(self):
        return self._do_test(mode="slow-text")

    def test_slow_sender_text(self):
        return self._do_test(mode="slow-sender-text")

    @inlineCallbacks
    def _do_test_fail(self, mode, failmode):
        assert mode in ("file", "directory")
        assert failmode in ("noclobber", "toobig")
        send_cfg = config("send")
        recv_cfg = config("receive")

        for cfg in [send_cfg, recv_cfg]:
            cfg.hide_progress = True
            cfg.relay_url = self.relayurl
            cfg.transit_helper = ""
            cfg.listen = False
            cfg.code = u"1-abc"
            cfg.stdout = io.StringIO()
            cfg.stderr = io.StringIO()

        send_dir = self.mktemp()
        os.mkdir(send_dir)
        receive_dir = self.mktemp()
        os.mkdir(receive_dir)
        recv_cfg.accept_file = True  # don't ask for permission

        if mode == "file":
            message = "test message\n"
            send_cfg.what = receive_name = send_filename = "testfile"
            fn = os.path.join(send_dir, send_filename)
            with open(fn, "w") as f:
                f.write(message)
            size = os.stat(fn).st_size

        elif mode == "directory":
            # $send_dir/
            # $send_dir/$dirname/
            # $send_dir/$dirname/[12345]
            # cd $send_dir && wormhole send $dirname
            # cd $receive_dir && wormhole receive
            # expect: $receive_dir/$dirname/[12345]

            size = 0
            send_cfg.what = receive_name = send_dirname = "testdir"
            os.mkdir(os.path.join(send_dir, send_dirname))
            for i in range(5):
                path = os.path.join(send_dir, send_dirname, str(i))
                with open(path, "w") as f:
                    f.write("test message %d\n" % i)
                size += os.stat(path).st_size

        if failmode == "noclobber":
            PRESERVE = "don't clobber me\n"
            clobberable = os.path.join(receive_dir, receive_name)
            with open(clobberable, "w") as f:
                f.write(PRESERVE)

        send_cfg.cwd = send_dir
        send_d = cmd_send.send(send_cfg)

        recv_cfg.cwd = receive_dir
        receive_d = cmd_receive.receive(recv_cfg)

        # both sides will fail
        if failmode == "noclobber":
            free_space = 10000000
        else:
            free_space = 0
        with mock.patch(
                "wormhole.cli.cmd_receive.estimate_free_space",
                return_value=free_space):
            f = yield self.assertFailure(send_d, TransferError)
            self.assertEqual(
                str(f), "remote error, transfer abandoned: transfer rejected")
            f = yield self.assertFailure(receive_d, TransferError)
            self.assertEqual(str(f), "transfer rejected")

        send_stdout = send_cfg.stdout.getvalue()
        send_stderr = send_cfg.stderr.getvalue()
        receive_stdout = recv_cfg.stdout.getvalue()
        receive_stderr = recv_cfg.stderr.getvalue()

        # all output here comes from a StringIO, which uses \n for
        # newlines, even if we're on windows
        NL = "\n"

        self.maxDiff = None  # show full output for assertion failures

        self.assertEqual(send_stdout, "")
        self.assertEqual(receive_stdout, "")

        # check sender
        if mode == "file":
            self.failUnlessIn("Sending {size:s} file named '{name}'{NL}"
                              .format(
                                  size=naturalsize(size),
                                  name=send_filename,
                                  NL=NL), send_stderr)
            self.failUnlessIn("Wormhole code is: {code}{NL}"
                              "On the other computer, please run:{NL}{NL}"
                              "wormhole receive {code}{NL}".format(
                                  code=send_cfg.code, NL=NL), send_stderr)
            self.failIfIn(
                "File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}".format(NL=NL),
                send_stderr)
        elif mode == "directory":
            self.failUnlessIn("Sending directory", send_stderr)
            self.failUnlessIn("named 'testdir'", send_stderr)
            self.failUnlessIn("Wormhole code is: {code}{NL}"
                              "On the other computer, please run:{NL}{NL}"
                              "wormhole receive {code}{NL}".format(
                                  code=send_cfg.code, NL=NL), send_stderr)
            self.failIfIn(
                "File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}".format(NL=NL),
                send_stderr)

        # check receiver
        if mode == "file":
            self.failIfIn("Received file written to ", receive_stderr)
            if failmode == "noclobber":
                self.failUnlessIn(
                    "Error: "
                    "refusing to overwrite existing 'testfile'{NL}"
                    .format(NL=NL),
                    receive_stderr)
            else:
                self.failUnlessIn(
                    "Error: "
                    "insufficient free space (0B) for file ({size:d}B){NL}"
                    .format(NL=NL, size=size), receive_stderr)
        elif mode == "directory":
            self.failIfIn(
                "Received files written to {name}".format(name=receive_name),
                receive_stderr)
            # want = (r"Receiving directory \(\d+ \w+\) into: {name}/"
            #        .format(name=receive_name))
            # self.failUnless(re.search(want, receive_stderr),
            #                (want, receive_stderr))
            if failmode == "noclobber":
                self.failUnlessIn(
                    "Error: "
                    "refusing to overwrite existing 'testdir'{NL}"
                    .format(NL=NL),
                    receive_stderr)
            else:
                self.failUnlessIn(("Error: "
                                   "insufficient free space (0B) for directory"
                                   " ({size:d}B){NL}").format(
                                       NL=NL, size=size), receive_stderr)

        if failmode == "noclobber":
            fn = os.path.join(receive_dir, receive_name)
            self.failUnless(os.path.exists(fn))
            with open(fn, "r") as f:
                self.failUnlessEqual(f.read(), PRESERVE)

    def test_fail_file_noclobber(self):
        return self._do_test_fail("file", "noclobber")

    def test_fail_directory_noclobber(self):
        return self._do_test_fail("directory", "noclobber")

    def test_fail_file_toobig(self):
        return self._do_test_fail("file", "toobig")

    def test_fail_directory_toobig(self):
        return self._do_test_fail("directory", "toobig")


class ZeroMode(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_text(self):
        send_cfg = config("send")
        recv_cfg = config("receive")
        message = "textponies"

        for cfg in [send_cfg, recv_cfg]:
            cfg.hide_progress = True
            cfg.relay_url = self.relayurl
            cfg.transit_helper = ""
            cfg.listen = True
            cfg.zeromode = True
            cfg.stdout = io.StringIO()
            cfg.stderr = io.StringIO()

        send_cfg.text = message

        # send_cfg.cwd = send_dir
        # recv_cfg.cwd = receive_dir

        send_d = cmd_send.send(send_cfg)
        receive_d = cmd_receive.receive(recv_cfg)

        yield gatherResults([send_d, receive_d], True)

        send_stdout = send_cfg.stdout.getvalue()
        send_stderr = send_cfg.stderr.getvalue()
        receive_stdout = recv_cfg.stdout.getvalue()
        receive_stderr = recv_cfg.stderr.getvalue()

        # all output here comes from a StringIO, which uses \n for
        # newlines, even if we're on windows
        NL = "\n"

        self.maxDiff = None  # show full output for assertion failures

        self.assertEqual(send_stdout, "")

        # check sender
        expected = ("Sending text message ({bytes:d} Bytes){NL}"
                    "On the other computer, please run:{NL}"
                    "{NL}"
                    "wormhole receive -0{NL}"
                    "{NL}"
                    "text message sent{NL}").format(
                        bytes=len(message), code=send_cfg.code, NL=NL)
        self.failUnlessEqual(send_stderr, expected)

        # check receiver
        self.assertEqual(receive_stdout, message + NL)
        self.assertEqual(receive_stderr, "")


class NotWelcome(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        yield self._setup_relay(error="please upgrade XYZ")
        self.cfg = cfg = config("send")
        cfg.hide_progress = True
        cfg.listen = False
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    @inlineCallbacks
    def test_sender(self):
        self.cfg.text = "hi"
        self.cfg.code = u"1-abc"

        send_d = cmd_send.send(self.cfg)
        f = yield self.assertFailure(send_d, WelcomeError)
        self.assertEqual(str(f), "please upgrade XYZ")

    @inlineCallbacks
    def test_receiver(self):
        self.cfg.code = u"1-abc"

        receive_d = cmd_receive.receive(self.cfg)
        f = yield self.assertFailure(receive_d, WelcomeError)
        self.assertEqual(str(f), "please upgrade XYZ")


class NoServer(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        yield self._setup_relay(None)
        yield self._relay_server.disownServiceParent()

    @inlineCallbacks
    def test_sender(self):
        cfg = config("send")
        cfg.hide_progress = True
        cfg.listen = False
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

        cfg.text = "hi"
        cfg.code = u"1-abc"

        send_d = cmd_send.send(cfg)
        e = yield self.assertFailure(send_d, ServerConnectionError)
        self.assertIsInstance(e.reason, ConnectionRefusedError)

    @inlineCallbacks
    def test_sender_allocation(self):
        cfg = config("send")
        cfg.hide_progress = True
        cfg.listen = False
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

        cfg.text = "hi"

        send_d = cmd_send.send(cfg)
        e = yield self.assertFailure(send_d, ServerConnectionError)
        self.assertIsInstance(e.reason, ConnectionRefusedError)

    @inlineCallbacks
    def test_receiver(self):
        cfg = config("receive")
        cfg.hide_progress = True
        cfg.listen = False
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

        cfg.code = u"1-abc"

        receive_d = cmd_receive.receive(cfg)
        e = yield self.assertFailure(receive_d, ServerConnectionError)
        self.assertIsInstance(e.reason, ConnectionRefusedError)


class Cleanup(ServerBase, unittest.TestCase):
    def make_config(self):
        cfg = config("send")
        # common options for all tests in this suite
        cfg.hide_progress = True
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()
        return cfg

    @inlineCallbacks
    @mock.patch('sys.stdout')
    def test_text(self, stdout):
        # the rendezvous channel should be deleted after success
        cfg = self.make_config()
        cfg.text = "hello"
        cfg.code = u"1-abc"

        send_d = cmd_send.send(cfg)
        receive_d = cmd_receive.receive(cfg)

        yield send_d
        yield receive_d

        cids = self._rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        self.assertEqual(len(cids), 0)

    @inlineCallbacks
    def test_text_wrong_password(self):
        # if the password was wrong, the rendezvous channel should still be
        # deleted
        send_cfg = self.make_config()
        send_cfg.text = "secret message"
        send_cfg.code = u"1-abc"
        send_d = cmd_send.send(send_cfg)

        rx_cfg = self.make_config()
        rx_cfg.code = u"1-WRONG"
        receive_d = cmd_receive.receive(rx_cfg)

        # both sides should be capable of detecting the mismatch
        yield self.assertFailure(send_d, WrongPasswordError)
        yield self.assertFailure(receive_d, WrongPasswordError)

        cids = self._rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        self.assertEqual(len(cids), 0)


class ExtractFile(unittest.TestCase):
    def test_filenames(self):
        args = mock.Mock()
        args.relay_url = u""
        ef = cmd_receive.Receiver(args)._extract_file
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
        zi.filename = "haha//root"  # abspath squashes this, hopefully zipfile
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


class AppID(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def setUp(self):
        yield super(AppID, self).setUp()
        self.cfg = cfg = config("send")
        # common options for all tests in this suite
        cfg.hide_progress = True
        cfg.relay_url = self.relayurl
        cfg.transit_helper = ""
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    @inlineCallbacks
    def test_override(self):
        # make sure we use the overridden appid, not the default
        self.cfg.text = "hello"
        self.cfg.appid = u"appid2"
        self.cfg.code = u"1-abc"

        send_d = cmd_send.send(self.cfg)
        receive_d = cmd_receive.receive(self.cfg)

        yield send_d
        yield receive_d

        used = self._usage_db.execute("SELECT DISTINCT `app_id`"
                                      " FROM `nameplates`").fetchall()
        self.assertEqual(len(used), 1, used)
        self.assertEqual(used[0]["app_id"], u"appid2")


class Welcome(unittest.TestCase):
    def do(self, welcome_message, my_version="2.0"):
        stderr = io.StringIO()
        welcome.handle_welcome(welcome_message, "url", my_version, stderr)
        return stderr.getvalue()

    def test_empty(self):
        stderr = self.do({})
        self.assertEqual(stderr, "")

    def test_version_current(self):
        stderr = self.do({"current_cli_version": "2.0"})
        self.assertEqual(stderr, "")

    def test_version_old(self):
        stderr = self.do({"current_cli_version": "3.0"})
        expected = ("Warning: errors may occur unless both sides are"
                    " running the same version\n"
                    "Server claims 3.0 is current, but ours is 2.0\n")
        self.assertEqual(stderr, expected)

    def test_version_unreleased(self):
        stderr = self.do(
            {
                "current_cli_version": "3.0"
            }, my_version="2.5+middle.something")
        self.assertEqual(stderr, "")

    def test_motd(self):
        stderr = self.do({"motd": "hello"})
        self.assertEqual(stderr, "Server (at url) says:\n hello\n")


class Dispatch(unittest.TestCase):
    @inlineCallbacks
    def test_success(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()
        called = []

        def fake():
            called.append(1)

        yield cli._dispatch_command(reactor, cfg, fake)
        self.assertEqual(called, [1])
        self.assertEqual(cfg.stderr.getvalue(), "")

    @inlineCallbacks
    def test_timing(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()
        cfg.timing = mock.Mock()
        cfg.dump_timing = "filename"

        def fake():
            pass

        yield cli._dispatch_command(reactor, cfg, fake)
        self.assertEqual(cfg.stderr.getvalue(), "")
        self.assertEqual(cfg.timing.mock_calls[-1],
                         mock.call.write("filename", cfg.stderr))

    @inlineCallbacks
    def test_wrong_password_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise WrongPasswordError("abcd")

        yield self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = fill("ERROR: " + dedent(WrongPasswordError.__doc__)) + "\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @inlineCallbacks
    def test_welcome_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise WelcomeError("abcd")

        yield self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = (
            fill("ERROR: " + dedent(WelcomeError.__doc__)) + "\n\nabcd\n")
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @inlineCallbacks
    def test_transfer_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise TransferError("abcd")

        yield self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = "TransferError: abcd\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @inlineCallbacks
    def test_server_connection_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise ServerConnectionError("URL", ValueError("abcd"))

        yield self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = fill(
            "ERROR: " + dedent(ServerConnectionError.__doc__)) + "\n"
        expected += "(relay URL was URL)\n"
        expected += "abcd\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @inlineCallbacks
    def test_other_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise ValueError("abcd")

        # I'm seeing unicode problems with the Failure().printTraceback, and
        # the output would be kind of unpredictable anyways, so we'll mock it
        # out here.
        f = mock.Mock()

        def mock_print(file):
            file.write(u"<TRACEBACK>\n")

        f.printTraceback = mock_print
        with mock.patch("wormhole.cli.cli.Failure", return_value=f):
            yield self.assertFailure(
                cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = "<TRACEBACK>\nERROR: abcd\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)


class Help(unittest.TestCase):
    def _check_top_level_help(self, got):
        # the main wormhole.cli.cli.wormhole docstring should be in the
        # output, but formatted differently
        self.assertIn("Create a Magic Wormhole and communicate through it.",
                      got)
        self.assertIn("--relay-url", got)
        self.assertIn("Receive a text message, file, or directory", got)

    def test_help(self):
        result = CliRunner().invoke(cli.wormhole, ["help"])
        self._check_top_level_help(result.output)
        self.assertEqual(result.exit_code, 0)

    def test_dash_dash_help(self):
        result = CliRunner().invoke(cli.wormhole, ["--help"])
        self._check_top_level_help(result.output)
        self.assertEqual(result.exit_code, 0)
