import builtins
import io
import os
import re
import stat
import sys
import tempfile
import zipfile
from functools import partial
from textwrap import dedent, fill

from click import UsageError
from click.testing import CliRunner
from humanize import naturalsize
from twisted.internet import endpoints, reactor
from twisted.internet.defer import gatherResults, CancelledError, ensureDeferred
from twisted.internet.error import ConnectionRefusedError
from twisted.internet.utils import getProcessOutputAndValue
from twisted.python import log, procutils
from zope.interface import implementer

from unittest import mock

import pytest
import pytest_twisted

from .. import __version__
from .._interfaces import ITorManager
from ..cli import cli, cmd_receive, cmd_send, welcome
from ..errors import (ServerConnectionError, TransferError,
                      UnsendableFileError, WelcomeError, WrongPasswordError)
from .common import config, setup_mailbox


def build_offer(args):
    s = cmd_send.Sender(args, None)
    return s._build_offer()


def maybe_delete(fn):
    if os.path.exists(fn):
        os.unlink(fn)


def create_config():
    cfg = config("send")
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()
    return cfg


def test_text_offer():
    cfg = create_config()
    cfg.text = message = "blah blah blah ponies"
    d, fd_to_send = build_offer(cfg)

    assert "message" in d
    assert "file" not in d
    assert "directory" not in d
    assert d["message"] == message
    assert fd_to_send is None


def test_file_offer(tmpdir_factory):
    cfg = create_config()
    cfg.what = filename = "my file"
    message = b"yay ponies\n"
    send_dir = tmpdir_factory.mktemp("sendfile")
    abs_filename = os.path.join(send_dir, filename)
    with open(abs_filename, "wb") as f:
        f.write(message)

    cfg.cwd = send_dir
    d, fd_to_send = build_offer(cfg)

    assert "message" not in d
    assert "file" in d
    assert "directory" not in d
    assert d["file"]["filesize"] == len(message)
    assert d["file"]["filename"] == filename
    assert fd_to_send.tell() == 0
    assert fd_to_send.read() == message


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason="host OS does not support symlinks")
def _create_broken_symlink(cfg, parent_dir):
    send_dir = "dirname"
    os.mkdir(os.path.join(parent_dir, send_dir))
    os.symlink('/non/existent/file',
               os.path.join(parent_dir, send_dir, 'linky'))

    send_dir_arg = send_dir
    cfg.what = send_dir_arg
    cfg.cwd = parent_dir


def test_broken_symlink_raises_err(tmpdir_factory):
    cfg = create_config()
    _create_broken_symlink(cfg, tmpdir_factory.mktemp("broken_sym"))
    cfg.ignore_unsendable_files = False
    with pytest.raises(UnsendableFileError) as e:
        build_offer(cfg)

    # On english distributions of Linux, this will be
    # "linky: No such file or directory", but the error may be
    # different on Windows and other locales and/or Unix variants, so
    # we'll just assert the part we know about.
    assert "linky: " in str(e)


def test_broken_symlink_is_ignored(tmpdir_factory):
    cfg = create_config()
    _create_broken_symlink(cfg, tmpdir_factory.mktemp("broken_sym_ign"))
    cfg.ignore_unsendable_files = True
    d, fd_to_send = build_offer(cfg)
    assert '(ignoring error)' in cfg.stderr.getvalue()
    assert d['directory']['numfiles'] == 0
    assert d['directory']['numbytes'] == 0


def test_missing_file(tmpdir_factory):
    cfg = create_config()
    cfg.what = filename = "missing"
    send_dir = tmpdir_factory.mktemp("missing_file")
    cfg.cwd = send_dir

    with pytest.raises(TransferError) as e:
        build_offer(cfg)
    assert str(e.value) == f"Cannot send: no file/directory named '{filename}'"


def _do_test_directory(parent_dir, addslash):
    send_dir = "dirname"
    os.mkdir(os.path.join(parent_dir, send_dir))
    ponies = [str(i) for i in range(5)]
    for p in ponies:
        with open(os.path.join(parent_dir, send_dir, p), "wb") as f:
            f.write(f"{p} ponies\n".encode("ascii"))

    send_dir_arg = send_dir
    if addslash:
        send_dir_arg += os.sep
    cfg = create_config()
    cfg.what = send_dir_arg
    cfg.cwd = parent_dir

    d, fd_to_send = build_offer(cfg)

    assert "message" not in d
    assert "file" not in d
    assert "directory" in d
    assert d["directory"]["dirname"] == send_dir
    assert d["directory"]["mode"] == "zipfile/deflated"
    assert d["directory"]["numfiles"] == 5
    assert "numbytes" in d["directory"]
    assert isinstance(d["directory"]["numbytes"], int)

    zdata = b"".join(fd_to_send)
    assert len(zdata) == d["directory"]["zipsize"]
    with zipfile.ZipFile(io.BytesIO(zdata), "r") as zf:
        zipnames = zf.namelist()
        assert list(sorted(ponies)) == list(sorted(zipnames))
        for name in zipnames:
            contents = zf.open(name, "r").read()
            assert (f"{name} ponies\n").encode("ascii") == \
                             contents


def test_directory_simple(tmpdir_factory):
    return _do_test_directory(tmpdir_factory.mktemp("dir"), addslash=False)


def test_directory_addslash_simple(tmpdir_factory):
    return _do_test_directory(tmpdir_factory.mktemp("addslash"), addslash=True)


def test_unknown(request, tmpdir_factory):
    cfg = create_config()
    cfg.what = filename = "unknown"
    send_dir = tmpdir_factory.mktemp("unknown")
    abs_filename = os.path.abspath(os.path.join(send_dir, filename))
    cfg.cwd = send_dir

    try:
        os.mkfifo(abs_filename)
    except AttributeError:
        return pytest.skip("is mkfifo supported on this platform?")

    # Delete the named pipe for the sake of users who might run "pip
    # wheel ." in this directory later. That command wants to copy
    # everything into a tempdir before building a wheel, and the
    # shutil.copy_tree() is uses can't handle the named pipe.
    request.addfinalizer(partial(maybe_delete, abs_filename))

    assert not os.path.isfile(abs_filename)
    assert not os.path.isdir(abs_filename)

    with pytest.raises(TypeError) as e:
        build_offer(cfg)
    assert str(e.value) == f"'{filename}' is neither file nor directory"


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason="host OS does not support symlinks")
def test_symlink(tmpdir_factory):
    # build A/B1 -> B2 (==A/B2), and A/B2/C.txt
    parent_dir = tmpdir_factory.mktemp("symlink_parent")
    os.mkdir(os.path.join(parent_dir, "B2"))
    with open(os.path.join(parent_dir, "B2", "C.txt"), "wb") as f:
        f.write(b"success")
    os.symlink("B2", os.path.join(parent_dir, "B1"))
    # now send "B1/C.txt" from A, and it should get the right file
    cfg = create_config()
    cfg.cwd = parent_dir
    cfg.what = os.path.join("B1", "C.txt")
    d, fd_to_send = build_offer(cfg)
    assert d["file"]["filename"] == "C.txt"
    assert fd_to_send.read() == b"success"


# ntpath.py's realpath() is built out of normpath(), and does not
# follow symlinks properly, so this test always fails. "wormhole send
# PATH" on windows will do the wrong thing. See
# https://bugs.python.org/issue9949" for details. I'm making this a
# TODO instead of a SKIP because 1: this causes an observable
# misbehavior (albeit in rare circumstances), 2: it probably used to
# work (sometimes, but not in #251). See cmd_send.py for more notes.
@pytest.mark.skipif(os.name == "nt", reason="host OS has broken os.path.realpath()")
@pytest.mark.skipif(not hasattr(os, 'symlink'), reason="host OS does not support symlinks")
def test_symlink_collapse(tmpdir_factory):
    cfg = create_config()
    # build A/B1, A/B1/D.txt
    # A/B2/C2, A/B2/D.txt
    # symlink A/B1/C1 -> A/B2/C2
    parent_dir = tmpdir_factory.mktemp("parent")
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
    cfg.cwd = parent_dir
    cfg.what = os.path.join("B1", "C1", os.pardir, "D.txt")
    d, fd_to_send = build_offer(cfg)
    assert d["file"]["filename"] == "D.txt"
    assert fd_to_send.read() == b"success"


async def _find_utf8_locale():
    """
    Click really wants to be running under a unicode-capable locale,
    especially on python3. macOS has en-US.UTF-8 but not C.UTF-8, and
    most linux boxes have C.UTF-8 but not en-US.UTF-8 . For tests,
    figure out which one is present and use that. For runtime, it's a
    mess, as really the user must take responsibility for setting their
    locale properly. I'm thinking of abandoning Click and going back to
    twisted.python.usage to avoid this problem in the future.
    """
    (out, err, rc) = await getProcessOutputAndValue("locale", ["-a"])
    if rc != 0:
        log.msg(f"error running 'locale -a', rc={rc}")
        log.msg(f"stderr: {err}")
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


@pytest.fixture(scope="module")
def locale():
    if sys.platform == "win32":
        return "en_US.UTF-8"
    best_locale = pytest_twisted.blockon(
        ensureDeferred(_find_utf8_locale())
    )
    yield best_locale


@pytest.fixture(scope="module")
def wormhole_executable():
    """
    to make sure we're running the right executable (in a virtualenv),
    we require that our "wormhole" lives in the same directory as our
    "python"
    """
    locations = procutils.which("wormhole")
    if not locations:
        return pytest.skip("unable to find 'wormhole' in $PATH")
    wormhole = locations[0]
    if (os.path.dirname(os.path.abspath(wormhole)) != os.path.dirname(
            sys.executable)):
        log.msg(f"locations: {locations}")
        log.msg(f"sys.executable: {sys.executable}")
        return pytest.skip(
            f"found the wrong 'wormhole' in $PATH: {wormhole} {sys.executable}")
    return wormhole


@pytest.fixture(scope="module")
def scripts_env(wormhole_executable, locale):
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
    if not locale:
        return pytest.skip("unable to find UTF-8 locale")
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
        log.msg(f"stderr was {err}")
        last = err.strip().split("\n")[-1]
        assert False, f"wormhole not runnable: {last}"
    ver = out.decode("utf-8") or err
    assert ver.strip() == f"magic-wormhole {__version__}"
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
        mailbox,
        transiturl,
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
        cfg.relay_url = mailbox.url
        cfg.transit_helper = ""
        cfg.listen = True
        cfg.code = "1-abc"
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
        send_filename = "testfil\u00EB"  # e-with-diaeresis
        with open(os.path.join(send_dir, send_filename), "w") as f:
            f.write(message)
        send_cfg.what = send_filename
        receive_filename = send_filename

        recv_cfg.accept_file = False if mock_accept else True
        if override_filename:
            recv_cfg.output_file = receive_filename = "outfile"
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

        recv_cfg.accept_file = False if mock_accept else True
        if override_filename:
            recv_cfg.output_file = receive_dirname = "outdir"
        if overwrite:
            recv_cfg.output_file = receive_dirname
            existing_file = os.path.join(receive_dir, receive_dirname)
            with open(existing_file, 'w') as f:
                f.write('pls overwrite me')

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
            mailbox.url,
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
            mailbox.url,
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
            send_cfg.transit_helper = transiturl
            tx_tm = FakeTor()
            with mock.patch(
                    "wormhole.tor_manager.get_tor",
                    return_value=tx_tm,
            ) as mtx_tm:
                send_d = cmd_send.send(send_cfg)

            recv_cfg.tor = True
            recv_cfg.transit_helper = transiturl
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
                receive_d = cmd_receive.receive(recv_cfg, _debug_stash_wormhole=rxw)
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
                        assert mo, s
                        sender_verifier = mo.group(1)
                else:
                    await gatherResults([send_d, receive_d], True)

        if fake_tor:
            expected_endpoints = [("127.0.0.1", mailbox.port._realPortNumber, False)]
            if mode in ("file", "directory"):
                transitport = int(transiturl.split(":")[2])
                expected_endpoints.append(("127.0.0.1", transitport, False))
            tx_timing = mtx_tm.call_args[1]["timing"]
            assert tx_tm.endpoints == expected_endpoints
            assert mtx_tm.mock_calls == \
                [mock.call(reactor, False, None, timing=tx_timing)]
            rx_timing = mrx_tm.call_args[1]["timing"]
            assert rx_tm.endpoints == expected_endpoints
            assert mrx_tm.mock_calls == \
                [mock.call(reactor, False, None, timing=rx_timing)]

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

    assert send_stdout == "Note: code has been consumed and can no longer be used.\n"

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
        expected = "Sending {size:s} file named '{name}'{NL}".format(
            size=naturalsize(len(message)),
            name=send_filename,
            NL=NL)
        assert expected in send_stderr
        assert f"Wormhole code is: {send_cfg.code}{NL}" in send_stderr
        expected = ("On the other computer, please run:{NL}{NL}"
                    "wormhole receive {code}{NL}{NL}").format(code=send_cfg.code, NL=NL)
        assert expected in send_stderr
        assert ("File sent.. waiting for confirmation{NL}"
                "Confirmation received. Transfer complete.{NL}").format(NL=NL) in send_stderr

    elif mode == "directory":
        assert "Sending directory" in send_stderr
        assert "named 'testdir'" in send_stderr
        assert f"Wormhole code is: {send_cfg.code}{NL}" in send_stderr
        assert "On the other computer, please run:{NL}{NL}wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL) in send_stderr

        assert "File sent.. waiting for confirmation{NL}Confirmation received. Transfer complete.{NL}".format(NL=NL) in send_stderr

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
        assert receive_stdout == ""
        want = "Receiving file ({size:s}) into: {name!r}".format(
            size=naturalsize(len(message)),
            name=receive_filename,
        )
        assert want in receive_stderr
        assert "Received file written to:" in receive_stderr
        fn = os.path.join(receive_dir, receive_filename)
        assert os.path.exists(fn)
        with open(fn) as f:
            assert f.read() == message
    elif mode == "directory":
        assert receive_stdout == ""
        want = (r"Receiving directory \(\d+ \w+\) into: {name!r}/"
                .format(name=receive_dirname))
        assert re.search(want, receive_stderr), (want, receive_stderr)
        assert "Received files written to" in receive_stderr
        assert f"{receive_dirname!r}" in receive_stderr
        fn = os.path.join(receive_dir, receive_dirname)
        assert os.path.exists(fn), fn
        for i in range(5):
            fn = os.path.join(receive_dir, receive_dirname, str(i))
            with open(fn) as f:
                assert f.read() == message(i)
            assert modes[i] == stat.S_IMODE(
                os.stat(fn).st_mode)

async def test_text(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory)


async def test_text_subprocess(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, as_subprocess=True)


async def test_text_tor(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, fake_tor=True)


async def test_text_verify(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, verify=True)


async def test_file(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="file")


async def test_file_override(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="file", override_filename=True)


async def test_file_overwrite(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="file", overwrite=True)


async def test_file_overwrite_mock_accept(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="file", overwrite=True, mock_accept=True)


async def test_file_tor(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="file", fake_tor=True)


async def test_empty_file(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="empty-file")


async def test_directory(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="directory")


async def test_directory_addslash(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="directory", addslash=True)


async def test_directory_override(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="directory", override_filename=True)


async def test_directory_overwrite(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="directory", overwrite=True)


async def test_slow_text(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="slow-text")


async def test_slow_sender_text(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory):
    await _do_test(wormhole_executable, scripts_env, mailbox, transit_relay, tmpdir_factory, mode="slow-sender-text")


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
        cfg.code = "1-abc"
        cfg.stdout = io.StringIO()
        cfg.stderr = io.StringIO()

    send_dir = tmpdir_factory.mktemp(f"sendfail-{mode}-{failmode}")
    receive_dir = tmpdir_factory.mktemp(f"recvfail-{mode}-{failmode}")
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
        with pytest.raises(TransferError) as e:
            await send_d
        assert str(e.value) == "remote error, transfer abandoned: transfer rejected"

        with pytest.raises(TransferError) as e:
            await receive_d
        assert str(e.value) == "transfer rejected"

    send_stdout = send_cfg.stdout.getvalue()
    send_stderr = send_cfg.stderr.getvalue()
    receive_stdout = recv_cfg.stdout.getvalue()
    receive_stderr = recv_cfg.stderr.getvalue()

    # all output here comes from a StringIO, which uses \n for
    # newlines, even if we're on windows
    NL = "\n"

    assert send_stdout == "Note: code has been consumed and can no longer be used.\n"
    assert receive_stdout == ""

    # check sender
    if mode == "file":
        assert "Sending {size:s} file named '{name}'{NL}".format(
            size=naturalsize(size),
            name=send_filename,
            NL=NL) in send_stderr
        assert f"Wormhole code is: {send_cfg.code}{NL}" in send_stderr
        assert "On the other computer, please run:{NL}{NL}wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL) in send_stderr
    elif mode == "directory":
        assert "Sending directory" in send_stderr
        assert "named 'testdir'" in send_stderr
        assert f"Wormhole code is: {send_cfg.code}{NL}" in send_stderr
        assert "On the other computer, please run:{NL}{NL}wormhole receive {code}{NL}{NL}".format(code=send_cfg.code, NL=NL) in send_stderr

    # check receiver
    if mode == "file":
        assert "Received file written to:" not in receive_stderr
        if failmode == "noclobber":
            assert f"Error: refusing to overwrite existing 'testfile'{NL}" in receive_stderr
        else:
            assert f"Error: insufficient free space (0B) for file ({size:d}B){NL}" in receive_stderr
    elif mode == "directory":
        assert "Received files written to" not in receive_stderr
        # want = (r"Receiving directory \(\d+ \w+\) into: {name}/"
        #        .format(name=receive_name))
        # self.failUnless(re.search(want, receive_stderr),
        #                (want, receive_stderr))
        if failmode == "noclobber":
            assert f"Error: refusing to overwrite existing 'testdir'{NL}" in receive_stderr
        else:
            assert (
                "Error: "
                "insufficient free space (0B) for directory"
                " ({size:d}B){NL}").format(NL=NL, size=size) in receive_stderr

    if failmode == "noclobber":
        fn = os.path.join(receive_dir, receive_name)
        assert os.path.exists(fn)
        with open(fn) as f:
            assert f.read() == PRESERVE


async def test_fail_file_noclobber(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "file", "noclobber")


async def test_fail_directory_noclobber(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "directory", "noclobber")


async def test_fail_file_toobig(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "file", "toobig")


async def test_fail_directory_toobig(wormhole_executable, scripts_env, mailbox, tmpdir_factory):
    await _do_test_fail(wormhole_executable, scripts_env, mailbox.url, tmpdir_factory, "directory", "toobig")


@pytest_twisted.ensureDeferred
async def test_text_ponies(mailbox):
    send_cfg = config("send")
    recv_cfg = config("receive")
    message = "textponies"

    for cfg in [send_cfg, recv_cfg]:
        cfg.hide_progress = True
        cfg.relay_url = mailbox.url
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

    assert send_stdout == "Note: code has been consumed and can no longer be used.\n"

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


@pytest.fixture(scope="module")
def unwelcome_mailbox(reactor):
    mailbox = pytest_twisted.blockon(
        ensureDeferred(
            setup_mailbox(reactor, error="please upgrade XYZ")
        )
    )
    mailbox.service.startService()
    yield mailbox.url
    pytest_twisted.blockon(mailbox.service.stopService())


@pytest.fixture()
def unwelcome_config(unwelcome_mailbox):
    cfg = config("send")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = unwelcome_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()
    return cfg


@pytest_twisted.ensureDeferred
async def test_sender_unwelcome(unwelcome_config):
    unwelcome_config.text = "hi"
    unwelcome_config.code = "1-abc"
    unwelcome_config.verbose = True
    unwelcome_config.stdout = sys.stdout
    unwelcome_config.stderr = sys.stderr

    send_d = cmd_send.send(unwelcome_config)
    with pytest.raises(WelcomeError) as e:
        await send_d
    assert str(e.value) == "please upgrade XYZ"


@pytest_twisted.ensureDeferred
async def test_receiver_unwelcome(unwelcome_config):
    unwelcome_config.code = "1-abc"

    receive_d = cmd_receive.receive(unwelcome_config)
    with pytest.raises(WelcomeError) as e:
        await receive_d
    assert str(e.value) == "please upgrade XYZ"


@pytest.fixture(scope="module")
def no_mailbox(reactor):
    # original test created and then destroyed a mailbox server --
    # basically just to get a port number. Having a hard time
    # "creating and killing" correctly in here, so short-cutting to "a
    # port that shouldn't be listening"
    yield "ws://127.0.0.1:1/v1"


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
    cfg.code = "1-abc"

    with pytest.raises(ServerConnectionError) as e:
        await cmd_send.send(cfg)
    assert isinstance(e.value.reason, ConnectionRefusedError)


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
    assert isinstance(e.value.reason, ConnectionRefusedError)


@pytest_twisted.ensureDeferred
async def test_receiver(no_mailbox):
    cfg = config("receive")
    cfg.hide_progress = True
    cfg.listen = False
    cfg.relay_url = no_mailbox
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()

    cfg.code = "1-abc"

    with pytest.raises(ServerConnectionError) as e:
        await cmd_receive.receive(cfg)
    assert isinstance(e.value.reason, ConnectionRefusedError)


def create_named_config(name, url):
    cfg = config(name)
    # common options for all tests in this suite
    cfg.hide_progress = True
    cfg.relay_url = url
    cfg.transit_helper = ""
    cfg.stdout = io.StringIO()
    cfg.stderr = io.StringIO()
    return cfg


@pytest_twisted.ensureDeferred
async def test_text_send(mailbox):
    send_config = create_named_config("send", mailbox.url)
    recv_config = create_named_config("receive", mailbox.url)
    send_config.code = "1-test-situation"
    recv_config.code = "1-test-situation"
    with mock.patch('sys.stdout'):
        # the rendezvous channel should be deleted after success
        send_config.text = "some text to send"
        send_d = cmd_send.send(send_config)
        receive_d = cmd_receive.receive(recv_config)

        await send_d
        await receive_d

        cids = mailbox.rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
        assert len(cids) == 0


@pytest_twisted.ensureDeferred
async def test_text_wrong_password(mailbox):
    # if the password was wrong, the rendezvous channel should still be
    # deleted
    send_config = create_named_config("send", mailbox.url)
    send_config.code = "1-foo-bar"
    send_config.text = "some text to send"
    send_d = cmd_send.send(send_config)

    rx_cfg = create_named_config("send", mailbox.url)
    rx_cfg.code = "1-WRONG"
    receive_d = cmd_receive.receive(rx_cfg)

    # both sides should be capable of detecting the mismatch
    with pytest.raises(WrongPasswordError):
        await send_d
    with pytest.raises(WrongPasswordError):
        await receive_d

    cids = mailbox.rendezvous.get_app(cmd_send.APPID).get_nameplate_ids()
    assert len(cids) == 0


def test_filenames(tmpdir_factory):
    args = mock.Mock()
    args.relay_url = ""
    ef = cmd_receive.Receiver(args)._extract_file
    extract_dir = os.path.abspath(tmpdir_factory.mktemp("filenames"))

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
    assert "malicious zipfile" in str(e.value)


def test_existing_destdir(tmpdir_factory):
    """
    We should preserve user data when they specify an existing
    destination _directory_ via --output-file (whereas we overwrite
    files if explicitly specified like this)
    """
    args = mock.Mock()
    args.relay_url = ""
    tmpdir = tempfile.mkdtemp()
    args.cwd = os.getcwd()
    args.output_file = tmpdir
    cmd = cmd_receive.Receiver(args)

    s = cmd._decide_destname(None, "destination_file")
    assert s == os.path.join(tmpdir, "destination_file")


def test_not_remove_existing_destdir(tmpdir_factory):
    """
    Do not remove an entire existing directory.
    """
    args = mock.Mock()
    args.relay_url = ""
    tmpdir = tempfile.mkdtemp()
    args.cwd = os.getcwd()
    args.output_file = tmpdir
    cmd = cmd_receive.Receiver(args)
    with pytest.raises(cmd_receive.TransferRejectedError):
        cmd._remove_existing(tmpdir)


@pytest_twisted.ensureDeferred
async def test_override(request, reactor):
    # note: we do not use the "mailbox" fixture because that is
    # session-wide and we want to ensure that _JUST_ "appid2"
    # nameplates appear
    mailbox = await setup_mailbox(reactor)
    mailbox.service.startService()

    def cleanup():
        pytest_twisted.blockon(mailbox.service.stopService())
    request.addfinalizer(cleanup)

    cfg = create_named_config("send", mailbox.url)
    cfg.text = "hello"
    cfg.appid = "appid2"
    cfg.code = "1-abc"
    send_d = ensureDeferred(cmd_send.send(cfg))
    receive_d = ensureDeferred(cmd_receive.receive(cfg))

    await gatherResults([send_d, receive_d])

    used = mailbox.usage_db.execute(
        "SELECT DISTINCT `app_id` FROM `nameplates`").fetchall()
    assert len(used) == 1, f"Incorrect nameplates: {used}"
    assert used[0]["app_id"] == "appid2"


def _welcome_test(welcome_message, my_version="2.0"):
    stderr = io.StringIO()
    welcome.handle_welcome(welcome_message, "url", my_version, stderr)
    return stderr.getvalue()


def test_empty():
    stderr = _welcome_test({})
    assert stderr == ""


def test_version_current():
    stderr = _welcome_test({"current_cli_version": "2.0"})
    assert stderr == ""


def test_version_old():
    stderr = _welcome_test({"current_cli_version": "3.0"})
    expected = ("Warning: errors may occur unless both sides are"
                " running the same version\n"
                "Server claims 3.0 is current, but ours is 2.0\n")
    assert stderr == expected


def test_version_unreleased():
    stderr = _welcome_test(
        {
            "current_cli_version": "3.0"
        }, my_version="2.5+middle.something")
    assert stderr == ""


def test_motd():
    stderr = _welcome_test({"motd": "hello"})
    assert stderr == "Server (at url) says:\n hello\n"


@pytest_twisted.ensureDeferred
async def test_success(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()
    called = []

    def fake():
        called.append(1)

    await cli._dispatch_command(reactor, cfg, fake)
    assert called == [1]
    assert cfg.stderr.getvalue() == ""


@pytest_twisted.ensureDeferred
async def test_timing(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()
    cfg.timing = mock.Mock()
    cfg.dump_timing = "filename"

    def fake():
        pass

    await cli._dispatch_command(reactor, cfg, fake)
    assert cfg.stderr.getvalue() == ""
    assert cfg.timing.mock_calls[-1] == \
                     mock.call.write("filename", cfg.stderr)


def test_debug_state_invalid_machine():
    cfg = cli.Config()
    with pytest.raises(UsageError):
        cfg.debug_state = "ZZZ"


@pytest_twisted.ensureDeferred
async def test_debug_state_send(mailbox):
    args = create_named_config("send", mailbox.url)
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
    assert "send.B[S0_empty].close" in \
        args.stdout.getvalue()


@pytest_twisted.ensureDeferred
async def test_debug_state_receive(mailbox):
    args = create_named_config("receive", mailbox.url)
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
    assert "recv.B[S0_empty].close" in \
        args.stdout.getvalue()


@pytest_twisted.ensureDeferred
async def test_wrong_password_error(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()

    def fake():
        raise WrongPasswordError("abcd")

    with pytest.raises(SystemExit):
        await cli._dispatch_command(reactor, cfg, fake)
    expected = fill("ERROR: " + dedent(WrongPasswordError.__doc__)) + "\n"
    assert cfg.stderr.getvalue() == expected


@pytest_twisted.ensureDeferred
async def test_welcome_error(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()

    def fake():
        raise WelcomeError("abcd")

    with pytest.raises(SystemExit):
        await cli._dispatch_command(reactor, cfg, fake)
    expected = (
        fill("ERROR: " + dedent(WelcomeError.__doc__)) + "\n\nabcd\n")
    assert cfg.stderr.getvalue() == expected


@pytest_twisted.ensureDeferred
async def test_transfer_error(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()

    def fake():
        raise TransferError("abcd")

    with pytest.raises(SystemExit):
        await cli._dispatch_command(reactor, cfg, fake)
    expected = "TransferError: abcd\n"
    assert cfg.stderr.getvalue() == expected


@pytest_twisted.ensureDeferred
async def test_server_connection_error(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()

    def fake():
        raise ServerConnectionError("URL", ValueError("abcd"))

    with pytest.raises(SystemExit):
        await cli._dispatch_command(reactor, cfg, fake)
    expected = fill(
        "ERROR: " + dedent(ServerConnectionError.__doc__)) + "\n"
    expected += "(relay URL was URL)\n"
    expected += "abcd\n"
    assert cfg.stderr.getvalue() == expected


@pytest_twisted.ensureDeferred
async def test_other_error(mailbox):
    cfg = create_named_config("send", mailbox.url)
    cfg.stderr = io.StringIO()

    def fake():
        raise ValueError("abcd")

    # I'm seeing unicode problems with the Failure().printTraceback, and
    # the output would be kind of unpredictable anyways, so we'll mock it
    # out here.
    f = mock.Mock()

    def mock_print(file):
        file.write("<TRACEBACK>\n")

    f.printTraceback = mock_print
    with mock.patch("wormhole.cli.cli.Failure", return_value=f):
        with pytest.raises(SystemExit):
            await cli._dispatch_command(reactor, cfg, fake)
    expected = "<TRACEBACK>\nERROR: abcd\n"
    assert cfg.stderr.getvalue() == expected


def _check_top_level_help(got):
    # the main wormhole.cli.cli.wormhole docstring should be in the
    # output, but formatted differently
    assert "Create a Magic Wormhole and communicate through it." in got
    assert "--relay-url" in got
    assert "Receive a text message, file, or directory" in got


def test_help():
    result = CliRunner().invoke(cli.wormhole, ["help"])
    _check_top_level_help(result.output)
    assert result.exit_code == 0


def test_dash_dash_help():
    result = CliRunner().invoke(cli.wormhole, ["--help"])
    _check_top_level_help(result.output)
    assert result.exit_code == 0


def test_inconsistent_receive_code_length():
    """
    specifying --code-length without --allocate is an error
    """
    result = CliRunner().invoke(
        cli.wormhole,
        ["receive", "--code-length", "3", "2-foo-bar"]
    )
    assert result.exit_code != 0
    assert "Must use --allocate" in result.output


def test_inconsistent_receive_allocate():
    """
    specifying --allocate and a code is an error
    """
    result = CliRunner().invoke(
        cli.wormhole,
        ["receive", "--allocate", "2-foo-bar"]
    )
    assert result.exit_code != 0
    assert "Cannot specify a code" in result.output
