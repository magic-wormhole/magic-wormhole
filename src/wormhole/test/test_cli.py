import builtins
import io
import os
import re
import stat
import sys
import zipfile
from textwrap import dedent, fill

from click import UsageError
from click.testing import CliRunner
from humanize import naturalsize
from twisted.internet import endpoints, reactor
from twisted.internet.defer import gatherResults, CancelledError
from twisted.internet.error import ConnectionRefusedError
from twisted.internet.utils import getProcessOutputAndValue
from twisted.python import log, procutils
from twisted.trial import unittest
from zope.interface import implementer

from unittest import mock

import pytest
import pytest_twisted

from .. import __version__
from .._interfaces import ITorManager
from ..cli import cli, cmd_receive, cmd_send, welcome
from ..errors import (ServerConnectionError, TransferError,
                      UnsendableFileError, WelcomeError, WrongPasswordError)
from .common import config


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
        self.assertIsInstance(d["directory"]["numbytes"], int)

        zdata = b"".join(fd_to_send)
        self.assertEqual(len(zdata), d["directory"]["zipsize"])
        with zipfile.ZipFile(io.BytesIO(zdata), "r") as zf:
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

    @pytest_twisted.ensureDeferred
    async def find_utf8_locale(self):
        if sys.platform == "win32":
            return "en_US.UTF-8"
        if self._run_once:
            return self._best_locale
        self._best_locale = await self._find_utf8_locale()
        self._run_once = True
        return self._best_locale

    @pytest_twisted.ensureDeferred
    async def _find_utf8_locale(self):
        # Click really wants to be running under a unicode-capable locale,
        # especially on python3. macOS has en-US.UTF-8 but not C.UTF-8, and
        # most linux boxes have C.UTF-8 but not en-US.UTF-8 . For tests,
        # figure out which one is present and use that. For runtime, it's a
        # mess, as really the user must take responsibility for setting their
        # locale properly. I'm thinking of abandoning Click and going back to
        # twisted.python.usage to avoid this problem in the future.
        (out, err, rc) = await getProcessOutputAndValue("locale", ["-a"])
        if rc != 0:
            log.msg("error running 'locale -a', rc=%s" % (rc, ))
            log.msg("stderr: %s" % (err, ))
            return None
        out = out.decode("utf-8")  # make sure we get a string
        utf8_locales = {}
        for locale in out.splitlines():
            locale = locale.strip()
            if locale.lower().endswith((".utf-8", ".utf8")):
                utf8_locales[locale.lower()] = locale
        for wanted in ["C.utf8", "C.UTF-8", "en_US.utf8", "en_US.UTF-8"]:
            if wanted.lower() in utf8_locales:
                return utf8_locales[wanted.lower()]
        if utf8_locales:
            return list(utf8_locales.values())[0]
        return None


locale_finder = LocaleFinder()


@pytest.fixture(scope="module")
def wormhole_executable():
    """
    to make sure we're running the right executable (in a virtualenv),
    we require that our "wormhole" lives in the same directory as our
    "python"
    """
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


@pytest.fixture(scope="module")
def scripts_env(wormhole_executable):
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
    from twisted.internet.defer import ensureDeferred
    locale = pytest_twisted.blockon(
        ensureDeferred(locale_finder.find_utf8_locale())
    )
    if not locale:
        raise unittest.SkipTest("unable to find UTF-8 locale")
    locale_env = dict(LC_ALL=locale, LANG=locale)
    res = pytest_twisted.blockon(
        getProcessOutputAndValue(
            wormhole_executable,
            ["--version"],
            env=locale_env,
        )
    )
    out, err, rc = res
    if rc != 0:
        log.msg("wormhole not runnable in this tree:")
        log.msg("out", out)
        log.msg("err", err)
        log.msg("rc", rc)
        pytest.skip("wormhole is not runnable in this tree")
        return
    return locale_env


@pytest_twisted.ensureDeferred
async def test_version(wormhole_executable):
    """
    "wormhole" must be on the path, so e.g. "pip install -e ." in a
    virtualenv. This guards against an environment where the tests
    below might run the wrong executable.
    """

    # we must pass on the environment so that "something" doesn't
    # get sad about UTF8 vs. ascii encodings
    out, err, rc = await getProcessOutputAndValue(
        wormhole_executable, ["--version"], env=os.environ)
    err = err.decode("utf-8")
    if "DistributionNotFound" in err:
        log.msg("stderr was %s" % err)
        last = err.strip().split("\n")[-1]
        assert False, "wormhole not runnable: %s" % last
    ver = out.decode("utf-8") or err
    assert ver.strip() == "magic-wormhole {}".format(__version__)
    assert rc == 0


@implementer(ITorManager)
class FakeTor:
    # use normal endpoints, but record the fact that we were asked
    def __init__(self):
        self.endpoints = []

    def stream_via(self, host, port, tls=False):
        self.endpoints.append((host, port, tls))
        return endpoints.HostnameEndpoint(reactor, host, port)


def strip_deprecations(stderr, NL):
    lines = [line
             for line in stderr.split(NL)
             if not ("Python 2 is no longer supported" in line or
                     "from cryptography import utils" in line or
                     "support will be dropped in the next release of cryptography" in line
                     )
             ]
    return NL.join(lines)


# note: this is a straight re-factor from the UnitTest test, but it
# might be better to re-factor further? Can we take "setup" code and
# "teardown" code and provide helpers for those, to get rid of "mode="
# and put the actually-different test code in the tests?


@pytest_twisted.ensureDeferred
async def _do_test(
        wormhole_executable,
        scripts_env,
        relayurl,
        tmpdir_factory,
        as_subprocess=False,
        mode="text",
        addslash=False,
        override_filename=False,
        fake_tor=False,
        overwrite=False,
        mock_accept=False,
        verify=False):
    assert mode in ("text", "file", "empty-file", "directory", "slow-text",
                    "slow-sender-text")
    if fake_tor:
        assert not as_subprocess
    send_cfg = config("send")
    recv_cfg = config("receive")
    message = "blah blah blah ponies"

    for cfg in [send_cfg, recv_cfg]:
        cfg.hide_progress = True
        cfg.relay_url = relayurl
        cfg.transit_helper = ""
        cfg.listen = True
        cfg.code = u"1-abc"
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()
        cfg.verify = verify

    send_dir = tmpdir_factory.mktemp("sender")
    receive_dir = tmpdir_factory.mktemp("receiver")

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
        env = scripts_env.copy()
        env["_MAGIC_WORMHOLE_TEST_KEY_TIMER"] = "999999"
        env["_MAGIC_WORMHOLE_TEST_VERIFY_TIMER"] = "999999"
        send_args = [
            '--relay-url',
            relayurl,
            '--transit-helper',
            '',
            'send',
            '--hide-progress',
            '--code',
            send_cfg.code,
        ] + content_args

        send_d = getProcessOutputAndValue(
            wormhole_executable,
            send_args,
            path=send_dir,
            env=env,
        )
        recv_args = [
            '--relay-url',
            relayurl,
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
            wormhole_executable,
            recv_args,
            path=receive_dir,
            env=env,
        )

        (send_res, receive_res) = await gatherResults([send_d, receive_d],
                                                      True)
        send_stdout = send_res[0].decode("utf-8")
        send_stderr = send_res[1].decode("utf-8")
        send_rc = send_res[2]
        receive_stdout = receive_res[0].decode("utf-8")
        receive_stderr = receive_res[1].decode("utf-8")
        receive_rc = receive_res[2]
        NL = os.linesep
        send_stderr = strip_deprecations(send_stderr, NL)
        receive_stderr = strip_deprecations(receive_stderr, NL)
        assert send_rc == 0, f"send failed: {send_res}"
        assert receive_rc == 0, f"receive failed: {receive_res}"

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
                    await rxw[0].get_unverified_key()

        # The sender might fail, leaving the receiver hanging, or vice
        # versa. Make sure we don't wait on one side exclusively
        VERIFY_TIMER = 0 if mode == "slow-text" else 99999
        with mock.patch.object(cmd_receive, "VERIFY_TIMER", VERIFY_TIMER):
            with mock.patch.object(cmd_send, "VERIFY_TIMER", VERIFY_TIMER):
                if mock_accept or verify:
                    with mock.patch.object(builtins, 'input',
                            return_value='yes') as i:
                        await gatherResults([send_d, receive_d], True)
                    if verify:
                        s = i.mock_calls[0][1][0]
                        mo = re.search(r'^Verifier (\w+)\. ok\?', s)
                        self.assertTrue(mo, s)
                        sender_verifier = mo.group(1)
                else:
                    await gatherResults([send_d, receive_d], True)

        if fake_tor:
            expected_endpoints = [("127.0.0.1", self.rdv_ws_port, False)]
            if mode in ("file", "directory"):
                expected_endpoints.append(("127.0.0.1", self.transitport, False))
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

    key_established = ""
    if mode == "slow-text":
        key_established = "Key established, waiting for confirmation...\n"

    assert send_stdout == ""

    # check sender
    if mode == "text" or mode == "slow-text":
        snippets = [
            "Sending text message ({bytes:d} Bytes){NL}",
            "Wormhole code is: {code}{NL}",
            (
                "On the other computer, please run:{NL}{NL}"
                "wormhole receive {verify}{code}{NL}{NL}"
            ),
            "{KE}",
            "text message sent{NL}",
        ]
        for snippet in snippets:
            expected = snippet.format(
                bytes=len(message),
                verify="--verify " if verify else "",
                code=send_cfg.code,
                NL=NL,
                KE=key_established,
            )
            assert expected in send_stderr
    elif mode == "file":
        expected = u"Sending {size:s} file named '{name}'{NL}".format(
            size=naturalsize(len(message)),
            name=send_filename,
            NL=NL)
        assert expected in send_stderr
        assert u"Wormhole code is: {code}{NL}".format(code=send_cfg.code, NL=NL) in send_stderr
        expected = (u"On the other computer, please run:{NL}{NL}"
                    "wormhole receive {code}{NL}{NL}").format(code=send_cfg.code, NL=NL)
        assert expected in send_stderr
        assert (u"File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}").format(NL=NL) in send_stderr

    elif mode == "directory":
        self.failUnlessIn(u"Sending directory", send_stderr)
        self.failUnlessIn(u"named 'testdir'", send_stderr)
        self.failUnlessIn(
            u"Wormhole code is: {code}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
        self.failUnlessIn(
            u"On the other computer, please run:{NL}{NL}"
            "wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
        self.failUnlessIn(
            u"File sent.. waiting for confirmation{NL}"
            "Confirmation received. Transfer complete.{NL}".format(NL=NL),
            send_stderr)

    # check receiver
    if mode in ("text", "slow-text", "slow-sender-text"):
        assert receive_stdout == message + NL
        if mode == "text":
            if verify:
                mo = re.search(r'^Verifier (\w+)\.\s*$', receive_stderr)
                assert mo, receive_stderr
                receiver_verifier = mo.group(1)
                assert sender_verifier == receiver_verifier
            else:
                assert receive_stderr == ""
        elif mode == "slow-text":
            assert receive_stderr == key_established
        elif mode == "slow-sender-text":
            assert receive_stderr == "Waiting for sender...\n"
    elif mode == "file":
        self.failUnlessEqual(receive_stdout, "")
        self.failUnlessIn(u"Receiving file ({size:s}) into: {name!r}".format(
            size=naturalsize(len(message)), name=receive_filename),
            receive_stderr)
        self.failUnlessIn(u"Received file written to ", receive_stderr)
        fn = os.path.join(receive_dir, receive_filename)
        self.failUnless(os.path.exists(fn))
        with open(fn, "r") as f:
            self.failUnlessEqual(f.read(), message)
    elif mode == "directory":
        self.failUnlessEqual(receive_stdout, "")
        want = (r"Receiving directory \(\d+ \w+\) into: {name!r}/"
                .format(name=receive_dirname))
        self.failUnless(
            re.search(want, receive_stderr), (want, receive_stderr))
        self.failUnlessIn(
            u"Received files written to {name!r}"
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

async def test_text(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory)

async def test_text_subprocess(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, as_subprocess=True)

async def test_text_tor(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, fake_tor=True)

async def test_text_verify(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, verify=True)

async def test_file(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="file")

async def test_file_override(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="file", override_filename=True)

async def test_file_overwrite(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="file", overwrite=True)

async def test_file_overwrite_mock_accept(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="file", overwrite=True, mock_accept=True)

async def test_file_tor(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="file", fake_tor=True)

async def test_empty_file(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="empty-file")

async def test_directory(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="directory")

async def test_directory_addslash(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="directory", addslash=True)

async def test_directory_override(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="directory", override_filename=True)

async def test_directory_overwrite(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="directory", overwrite=True)

async def test_directory_overwrite_mock_accept(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(
        wormhole_executable,
        scripts_env,
        mailbox.url,
        tmpdir_factory,
        mode="directory",
        overwrite=True,
        mock_accept=True,
    )

async def test_slow_text(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="slow-text")

async def test_slow_sender_text(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, mode="slow-sender-text")


@pytest_twisted.ensureDeferred
async def _do_test_fail(wormhole_executable, scripts_env, relayurl, tmpdir_factory, mode, failmode):
    assert mode in ("file", "directory")
    assert failmode in ("noclobber", "toobig")
    send_cfg = config("send")
    recv_cfg = config("receive")

    for cfg in [send_cfg, recv_cfg]:
        cfg.hide_progress = True
        cfg.relay_url = relayurl
        cfg.transit_helper = ""
        cfg.listen = False
        cfg.code = u"1-abc"
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    send_dir = tmpdir_factory.mktemp(".")
    os.mkdir(send_dir)
    receive_dir = tmpdir_factory.mktemp(".")
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
        f = await self.assertFailure(send_d, TransferError)
        self.assertEqual(
            str(f), "remote error, transfer abandoned: transfer rejected")
        f = await self.assertFailure(receive_d, TransferError)
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
        self.failUnlessIn(
            u"Wormhole code is: {code}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
        self.failUnlessIn(
            u"On the other computer, please run:{NL}{NL}"
            "wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
        self.failIfIn(
            "File sent.. waiting for confirmation{NL}"
            "Confirmation received. Transfer complete.{NL}".format(NL=NL),
            send_stderr)
    elif mode == "directory":
        self.failUnlessIn("Sending directory", send_stderr)
        self.failUnlessIn("named 'testdir'", send_stderr)
        self.failUnlessIn(
            u"Wormhole code is: {code}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
        self.failUnlessIn(
            u"On the other computer, please run:{NL}{NL}"
            "wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL),
            send_stderr,
        )
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
            "Received files written to {name!r}".format(name=receive_name),
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

async def test_fail_file_noclobber(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_tests_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "file", "noclobber")

async def test_fail_directory_noclobber(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_tests_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "directory", "noclobber")

async def test_fail_file_toobig(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_tests_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "file", "toobig")

async def test_fail_directory_toobig(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_tests_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "directory", "toobig")


@pytest_twisted.ensureDeferred
async def test_text(mailbox):
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

    await gatherResults([send_d, receive_d], True)

    send_stdout = send_cfg.stdout.getvalue()
    send_stderr = send_cfg.stderr.getvalue()
    receive_stdout = recv_cfg.stdout.getvalue()
    receive_stderr = recv_cfg.stderr.getvalue()

    # all output here comes from a StringIO, which uses \n for
    # newlines, even if we're on windows
    NL = "\n"

    assert send_stdout == ""

    # check sender
    expected = ("Sending text message ({bytes:d} Bytes){NL}"
                "On the other computer, please run:{NL}"
                "{NL}"
                "wormhole receive -0{NL}"
                "{NL}"
                "text message sent{NL}").format(
                    bytes=len(message), NL=NL)
    assert send_stderr == expected
    # check receiver
    assert receive_stdout == message + NL
    assert receive_stderr == ""


from .common import setup_mailbox


@pytest.fixture(scope="module")
def unwelcome_mailbox(reactor):
    url, service = setup_mailbox(reactor, error="please upgrade XYZ")
    pytest_twisted.blockon(service.startService())
    yield url
    pytest_twisted.blockon(service.stopService())


@pytest.fixture()
def unwelcome_config(unwelcome_mailbox):
    cfg = config("send")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = unwelcome_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()


@pytest_twisted.ensureDeferred
async def test_sender(unwelcome_config):
    unwelcome_config.text = "hi"
    unwelcome_config.code = u"1-abc"

    send_d = cmd_send.send(unwelcome_config)
    f = await self.assertFailure(send_d, WelcomeError)
    self.assertEqual(str(f), "please upgrade XYZ")

@pytest_twisted.ensureDeferred
async def test_receiver(self):
    unwelcome_config.code = u"1-abc"

    receive_d = cmd_receive.receive(unwelcome_config)
    f = await self.assertFailure(receive_d, WelcomeError)
    self.assertEqual(str(f), "please upgrade XYZ")


@pytest.fixture(scope="module")
def no_mailbox(reactor):
    url, service = setup_mailbox(reactor)
    # the original tests these ported from did this ... seems like
    # overkill, if Twisted is "working properly" this will be the same
    # as selecting any non-listening port and just creating a url?
    pytest_twisted.blockon(service.startService())
    pytest_twisted.blockon(service.stopService())
    yield url


@pytest_twisted.ensureDeferred
async def test_sender(no_mailbox):
    cfg = config("send")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = no_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()

    cfg.text = "hi"
    cfg.code = u"1-abc"

    with pytest.raises(ServerConnectionError) as e:
        await cmd_send.send(cfg)
        assert isinstance(e.reason, ConnectionRefusedError)

@pytest_twisted.ensureDeferred
async def test_sender_allocation(no_mailbox):
    cfg = config("send")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = no_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()

    cfg.text = "hi"

    with pytest.raises(ServerConnectionError) as e:
        await cmd_send.send(cfg)
        assert isinstance(e.reason, ConnectionRefusedError)

@pytest_twisted.ensureDeferred
async def test_receiver(no_mailbox):
    cfg = config("receive")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = no_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()

    cfg.code = u"1-abc"

    with pytest.raises(ServerConnectionError) as e:
        await cmd_receive.receive(cfg)
        assert isinstance(e.reason, ConnectionRefusedError)


@pytest.fixture()
def send_config(mailbox):
    cfg = create_config("send", mailbox.url)
    cfg.allocate = True
    yield cfg


def create_config(name, url):
    cfg = config(name)
    # common options for all tests in this suite
    cfg.hide_progress = True
    cfg.relay_url = url
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()
    cfg.allocate = False
    return cfg


@pytest_twisted.ensureDeferred
async def test_text(send_config):
    with mock.patch('sys.stdout') as stdout:
        # the rendezvous channel should be deleted after success
        send_d = cmd_send.send(send_config)
        receive_d = cmd_receive.receive(send_config)

        await send_d
        await receive_d

        # XXX FIXME: hard-mode to reach in this far with current fixture
        ##cids = self._rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        ##assert len(cids) == 0


@pytest_twisted.ensureDeferred
async def test_text_wrong_password(send_config):
    # if the password was wrong, the rendezvous channel should still be
    # deleted
    send_d = cmd_send.send(send_config)

    rx_cfg = self.make_config()
    rx_cfg.code = u"1-WRONG"
    receive_d = cmd_receive.receive(rx_cfg)

    # both sides should be capable of detecting the mismatch
    await self.assertFailure(send_d, WrongPasswordError)
    await self.assertFailure(receive_d, WrongPasswordError)

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
            assert zf.extract.mock_calls == [mock.call(zi.filename, path=extract_dir)]
            assert chmod.mock_calls == [mock.call(expected, 5)]

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "../haha"
        with pytest.raises(ValueError) as e:
            ef(zf, zi, extract_dir)
            assert "malicious zipfile" in str(e)

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "haha//root"  # abspath squashes this, hopefully zipfile
        # does too
        zi.external_attr = 5 << 16
        expected = os.path.join(extract_dir, "haha", "root")
        with mock.patch.object(cmd_receive.os, "chmod") as chmod:
            ef(zf, zi, extract_dir)
            assert zf.extract.mock_calls, [mock.call(zi.filename, path=extract_dir)]
            assert chmod.mock_calls == [mock.call(expected, 5)]

        zf = mock.Mock()
        zi = mock.Mock()
        zi.filename = "/etc/passwd"
        with pytest.raises(ValueError) as e:
            ef(zf, zi, extract_dir)
            assert "malicious zipfile" in str(e)


@pytest_twisted.ensureDeferred
async def test_override(mailbox, send_config):
    from twisted.internet.defer import ensureDeferred
    send_config.text = "some text"
    send_d = ensureDeferred(cmd_send.send(send_config))
    receive_d = ensureDeferred(cmd_receive.receive(send_config))

    await gatherResults([send_d, receive_d])

    used = mailbox.usage_db.execute(
        "SELECT DISTINCT `app_id` FROM `nameplates`").fetchall()
    assert len(used) == 1, f"Incorrect nameplates: {used}"
    assert used[0]["app_id"] == u"appid2"


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
    @pytest_twisted.ensureDeferred
    async def test_success(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()
        called = []

        def fake():
            called.append(1)

        await cli._dispatch_command(reactor, cfg, fake)
        self.assertEqual(called, [1])
        self.assertEqual(cfg.stderr.getvalue(), "")

    @pytest_twisted.ensureDeferred
    async def test_timing(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()
        cfg.timing = mock.Mock()
        cfg.dump_timing = "filename"

        def fake():
            pass

        await cli._dispatch_command(reactor, cfg, fake)
        self.assertEqual(cfg.stderr.getvalue(), "")
        self.assertEqual(cfg.timing.mock_calls[-1],
                         mock.call.write("filename", cfg.stderr))

    def test_debug_state_invalid_machine(self):
        cfg = cli.Config()
        with self.assertRaises(UsageError):
            cfg.debug_state = "ZZZ"

    @pytest_twisted.ensureDeferred
    async def test_debug_state_send(self):
        args = config("send")
        args.debug_state = "B,N,M,S,O,K,SK,R,RC,L,C,T"
        args.stdout = io.StringIO()
        s = cmd_send.Sender(args, reactor)
        d = s.go()
        d.cancel()
        try:
            await d
        except CancelledError:
            pass
        # just check for at least one state-transition we expected to
        # get logged due to the --debug-state option
        self.assertIn(
            "send.B[S0_empty].close",
            args.stdout.getvalue(),
        )

    @pytest_twisted.ensureDeferred
    async def test_debug_state_receive(self):
        args = config("receive")
        args.debug_state = "B,N,M,S,O,K,SK,R,RC,L,C,T"
        args.stdout = io.StringIO()
        s = cmd_receive.Receiver(args, reactor)
        d = s.go()
        d.cancel()
        try:
            await d
        except CancelledError:
            pass
        # just check for at least one state-transition we expected to
        # get logged due to the --debug-state option
        self.assertIn(
            "recv.B[S0_empty].close",
            args.stdout.getvalue(),
        )

    @pytest_twisted.ensureDeferred
    async def test_wrong_password_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise WrongPasswordError("abcd")

        await self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = fill("ERROR: " + dedent(WrongPasswordError.__doc__)) + "\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @pytest_twisted.ensureDeferred
    async def test_welcome_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise WelcomeError("abcd")

        await self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = (
            fill("ERROR: " + dedent(WelcomeError.__doc__)) + "\n\nabcd\n")
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @pytest_twisted.ensureDeferred
    async def test_transfer_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise TransferError("abcd")

        await self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = "TransferError: abcd\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @pytest_twisted.ensureDeferred
    async def test_server_connection_error(self):
        cfg = config("send")
        cfg.stderr = io.StringIO()

        def fake():
            raise ServerConnectionError("URL", ValueError("abcd"))

        await self.assertFailure(
            cli._dispatch_command(reactor, cfg, fake), SystemExit)
        expected = fill(
            "ERROR: " + dedent(ServerConnectionError.__doc__)) + "\n"
        expected += "(relay URL was URL)\n"
        expected += "abcd\n"
        self.assertEqual(cfg.stderr.getvalue(), expected)

    @pytest_twisted.ensureDeferred
    async def test_other_error(self):
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
            await self.assertFailure(
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

    def test_inconsistent_receive_code_length(self):
        """
        specifying --code-length without --allocate is an error
        """
        result = CliRunner().invoke(
            cli.wormhole,
            ["receive", "--code-length", "3", "2-foo-bar"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Must use --allocate", result.output)

    def test_inconsistent_receive_allocate(self):
        """
        specifying --allocate and a code is an error
        """
        result = CliRunner().invoke(
            cli.wormhole,
            ["receive", "--allocate", "2-foo-bar"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Cannot specify a code", result.output)

