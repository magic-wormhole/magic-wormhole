from __future__ import print_function

import hashlib
import os
import sys
import tempfile
import zipfile

import six
from humanize import naturalsize
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.protocols import basic
from twisted.python import log
from wormhole import __version__, create

from ..errors import TransferError, UnsendableFileError
from ..transit import TransitSender
from ..util import bytes_to_dict, bytes_to_hexstr, dict_to_bytes
from .welcome import handle_welcome

APPID = u"lothar.com/wormhole/text-or-file-xfer"
VERIFY_TIMER = float(os.environ.get("_MAGIC_WORMHOLE_TEST_VERIFY_TIMER", 1.0))


def send(args, reactor=reactor):
    """I implement 'wormhole send'. I return a Deferred that fires with None
    (for success), or signals one of the following errors:
    * WrongPasswordError: the two sides didn't use matching passwords
    * Timeout: something didn't happen fast enough for our tastes
    * TransferError: the receiver rejected the transfer: verifier mismatch,
                     permission not granted, ack not successful.
    * any other error: something unexpected happened
    """
    return Sender(args, reactor).go()


class Sender:
    def __init__(self, args, reactor):
        self._args = args
        self._reactor = reactor
        self._tor = None
        self._timing = args.timing
        self._fd_to_send = None
        self._transit_sender = None

    @inlineCallbacks
    def go(self):
        assert isinstance(self._args.relay_url, type(u""))
        if self._args.tor:
            with self._timing.add("import", which="tor_manager"):
                from ..tor_manager import get_tor
            # For now, block everything until Tor has started. Soon: launch
            # tor in parallel with everything else, make sure the Tor object
            # can lazy-provide an endpoint, and overlap the startup process
            # with the user handing off the wormhole code
            self._tor = yield get_tor(
                reactor,
                self._args.launch_tor,
                self._args.tor_control_port,
                timing=self._timing)

        w = create(
            self._args.appid or APPID,
            self._args.relay_url,
            self._reactor,
            tor=self._tor,
            timing=self._timing)
        d = self._go(w)

        # if we succeed, we should close and return the w.close results
        # (which might be an error)
        @inlineCallbacks
        def _good(res):
            yield w.close()  # wait for ack
            returnValue(res)

        # if we raise an error, we should close and then return the original
        # error (the close might give us an error, but it isn't as important
        # as the original one)
        @inlineCallbacks
        def _bad(f):
            try:
                yield w.close()  # might be an error too
            except Exception:
                pass
            returnValue(f)

        d.addCallbacks(_good, _bad)
        yield d

    def _send_data(self, data, w):
        data_bytes = dict_to_bytes(data)
        w.send_message(data_bytes)

    @inlineCallbacks
    def _go(self, w):
        welcome = yield w.get_welcome()
        handle_welcome(welcome, self._args.relay_url, __version__,
                       self._args.stderr)

        # TODO: run the blocking zip-the-directory IO in a thread, let the
        # wormhole exchange happen in parallel
        offer, self._fd_to_send = self._build_offer()
        args = self._args

        other_cmd = u"wormhole receive"
        if args.verify:
            other_cmd = u"wormhole receive --verify"
        if args.zeromode:
            assert not args.code
            args.code = u"0-"
            other_cmd += u" -0"

        if args.code:
            w.set_code(args.code)
        else:
            w.allocate_code(args.code_length)

        code = yield w.get_code()
        if not args.zeromode:
            print(u"Wormhole code is: %s" % code, file=args.stderr)
            other_cmd += u" " + code
        print(u"On the other computer, please run:", file=args.stderr)
        print(u"", file=args.stderr)
        print(other_cmd, file=args.stderr)
        print(u"", file=args.stderr)
        # flush stderr so the code is displayed immediately
        args.stderr.flush()

        # We don't print a "waiting" message for get_unverified_key() here,
        # even though we do that in cmd_receive.py, because it's not at all
        # surprising to we waiting here for a long time. We'll sit in
        # get_unverified_key() until the receiver has typed in the code and
        # their PAKE message makes it to us.
        yield w.get_unverified_key()

        # TODO: don't stall on w.get_verifier() unless they want it
        def on_slow_connection():
            print(
                u"Key established, waiting for confirmation...",
                file=args.stderr)

        notify = self._reactor.callLater(VERIFY_TIMER, on_slow_connection)
        try:
            # The usual sender-chooses-code sequence means the receiver's
            # PAKE should be followed immediately by their VERSION, so
            # w.get_verifier() should fire right away. However if we're
            # using the offline-codes sequence, and the receiver typed in
            # their code first, and then they went offline, we might be
            # sitting here for a while, so printing the "waiting" message
            # seems like a good idea. It might even be appropriate to give up
            # after a while.
            verifier_bytes = yield w.get_verifier()  # might WrongPasswordError
        finally:
            if not notify.called:
                notify.cancel()

        if args.verify:
            self._check_verifier(w,
                                 verifier_bytes)  # blocks, can TransferError

        if self._fd_to_send:
            ts = TransitSender(
                args.transit_helper,
                no_listen=(not args.listen),
                tor=self._tor,
                reactor=self._reactor,
                timing=self._timing)
            self._transit_sender = ts

            # for now, send this before the main offer
            sender_abilities = ts.get_connection_abilities()
            sender_hints = yield ts.get_connection_hints()
            sender_transit = {
                "abilities-v1": sender_abilities,
                "hints-v1": sender_hints,
            }
            self._send_data({u"transit": sender_transit}, w)

            # TODO: move this down below w.get_message()
            transit_key = w.derive_key(APPID + "/transit-key",
                                       ts.TRANSIT_KEY_LENGTH)
            ts.set_transit_key(transit_key)

        self._send_data({"offer": offer}, w)

        want_answer = True

        while True:
            them_d_bytes = yield w.get_message()
            # TODO: get_message() fired, so get_verifier must have fired, so
            # now it's safe to use w.derive_key()
            them_d = bytes_to_dict(them_d_bytes)
            # print("GOT", them_d)
            recognized = False
            if u"error" in them_d:
                raise TransferError(
                    "remote error, transfer abandoned: %s" % them_d["error"])
            if u"transit" in them_d:
                recognized = True
                yield self._handle_transit(them_d[u"transit"])
            if u"answer" in them_d:
                recognized = True
                if not want_answer:
                    raise TransferError("duplicate answer")
                want_answer = True
                yield self._handle_answer(them_d[u"answer"])
                returnValue(None)
            if not recognized:
                log.msg("unrecognized message %r" % (them_d, ))

    def _check_verifier(self, w, verifier_bytes):
        verifier = bytes_to_hexstr(verifier_bytes)
        while True:
            ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                err = "sender rejected verification check, abandoned transfer"
                reject_data = dict_to_bytes({"error": err})
                w.send_message(reject_data)
                raise TransferError(err)

    def _handle_transit(self, receiver_transit):
        ts = self._transit_sender
        ts.add_connection_hints(receiver_transit.get("hints-v1", []))

    def _build_offer(self):
        offer = {}

        args = self._args
        text = args.text
        if text == "-":
            print(u"Reading text message from stdin..", file=args.stderr)
            text = sys.stdin.read()
        if not text and not args.what:
            text = six.moves.input("Text to send: ")

        if text is not None:
            print(
                u"Sending text message (%s)" % naturalsize(len(text)),
                file=args.stderr)
            offer = {"message": text}
            fd_to_send = None
            return offer, fd_to_send

        # click.Path (with resolve_path=False, the default) does not do path
        # resolution, so we must join it to cwd ourselves. We could use
        # resolve_path=True, but then it would also do os.path.realpath(),
        # which would replace the basename with the target of a symlink (if
        # any), which is not what I think users would want: if you symlink
        # X->Y and send X, you expect the recipient to save it in X, not Y.
        #
        # TODO: an open question is whether args.cwd (i.e. os.getcwd()) will
        # be unicode or bytes. We need it to be something that can be
        # os.path.joined with the unicode args.what .
        what = os.path.join(args.cwd, args.what)

        # We always tell the receiver to create a file (or directory) with the
        # same basename as what the local user typed, even if the local object
        # is a symlink to something with a different name. The normpath() is
        # there to remove trailing slashes.
        basename = os.path.basename(os.path.normpath(what))
        assert basename != "", what  # normpath shouldn't allow this

        # We use realpath() instead of normpath() to locate the actual
        # file/directory, because the path might contain symlinks, and
        # normpath() would collapse those before resolving them.
        # test_cli.OfferData.test_symlink_collapse tests this.

        # Unfortunately on windows, realpath() (on py3) is built out of
        # normpath() because of a py2-era belief that windows lacks a working
        # os.path.islink(): see https://bugs.python.org/issue9949 . The
        # consequence is that "wormhole send PATH" might send the wrong file,
        # if:
        # * we're running on windows
        # * PATH goes down through a symlink and then up with parent-directory
        #   navigation (".."), then back down again
        # * the back-down-again portion of the path also exists under the
        #   original directory (an error is thrown if not)

        # I'd like to fix this. The core issue is sending directories with a
        # trailing slash: we need to 1: open the right directory, and 2: strip
        # the right parent path out of the filenames we get from os.walk(). We
        # used to use what.rstrip() for this, but bug #251 reported this
        # failing on windows-with-bash. realpath() works in both those cases,
        # but fails with the up-down symlinks situation. I think we'll need to
        # find a third way to strip the trailing slash reliably in all
        # environments.

        what = os.path.realpath(what)
        if not os.path.exists(what):
            raise TransferError(
                "Cannot send: no file/directory named '%s'" % args.what)

        if os.path.isfile(what):
            # we're sending a file
            filesize = os.stat(what).st_size
            offer["file"] = {
                "filename": basename,
                "filesize": filesize,
            }
            print(
                u"Sending %s file named '%s'" % (naturalsize(filesize),
                                                 basename),
                file=args.stderr)
            fd_to_send = open(what, "rb")
            return offer, fd_to_send

        if os.path.isdir(what):
            print(u"Building zipfile..", file=args.stderr)
            # We're sending a directory. Create a zipfile in a tempdir and
            # send that.
            fd_to_send = tempfile.SpooledTemporaryFile()
            # workaround for https://bugs.python.org/issue26175 (STF doesn't
            # fully implement IOBase abstract class), which breaks the new
            # zipfile in py3.7.0 that expects .seekable
            if not hasattr(fd_to_send, "seekable"):
                # AFAICT all the filetypes that STF wraps can seek
                fd_to_send.seekable = lambda: True
            num_files = 0
            num_bytes = 0
            tostrip = len(what.split(os.sep))
            with zipfile.ZipFile(
                    fd_to_send,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    allowZip64=True) as zf:
                for path, dirs, files in os.walk(what):
                    # path always starts with args.what, then sometimes might
                    # have "/subdir" appended. We want the zipfile to contain
                    # "" or "subdir"
                    localpath = list(path.split(os.sep)[tostrip:])
                    for fn in files:
                        archivename = os.path.join(*tuple(localpath + [fn]))
                        localfilename = os.path.join(path, fn)
                        try:
                            zf.write(localfilename, archivename)
                            num_bytes += os.stat(localfilename).st_size
                            num_files += 1
                        except OSError as e:
                            errmsg = u"{}: {}".format(fn, e.strerror)
                            if self._args.ignore_unsendable_files:
                                print(
                                    u"{} (ignoring error)".format(errmsg),
                                    file=args.stderr)
                            else:
                                raise UnsendableFileError(errmsg)
            fd_to_send.seek(0, 2)
            filesize = fd_to_send.tell()
            fd_to_send.seek(0, 0)
            offer["directory"] = {
                "mode": "zipfile/deflated",
                "dirname": basename,
                "zipsize": filesize,
                "numbytes": num_bytes,
                "numfiles": num_files,
            }
            print(
                u"Sending directory (%s compressed) named '%s'" %
                (naturalsize(filesize), basename),
                file=args.stderr)
            return offer, fd_to_send

        raise TypeError("'%s' is neither file nor directory" % args.what)

    @inlineCallbacks
    def _handle_answer(self, them_answer):
        if self._fd_to_send is None:
            if them_answer["message_ack"] == "ok":
                print(u"text message sent", file=self._args.stderr)
                returnValue(None)  # terminates this function
            raise TransferError("error sending text: %r" % (them_answer, ))

        if them_answer.get("file_ack") != "ok":
            raise TransferError("ambiguous response from remote, "
                                "transfer abandoned: %s" % (them_answer, ))

        yield self._send_file()

    @inlineCallbacks
    def _send_file(self):
        ts = self._transit_sender

        self._fd_to_send.seek(0, 2)
        filesize = self._fd_to_send.tell()
        self._fd_to_send.seek(0, 0)

        record_pipe = yield ts.connect()
        self._timing.add("transit connected")
        # record_pipe should implement IConsumer, chunks are just records
        stderr = self._args.stderr
        print(u"Sending (%s).." % record_pipe.describe(), file=stderr)

        hasher = hashlib.sha256()
        progress = tqdm(
            file=stderr,
            disable=self._args.hide_progress,
            unit="B",
            unit_scale=True,
            total=filesize)

        def _count_and_hash(data):
            hasher.update(data)
            progress.update(len(data))
            return data

        fs = basic.FileSender()

        with self._timing.add("tx file"):
            with progress:
                if filesize:
                    # don't send zero-length files
                    yield fs.beginFileTransfer(
                        self._fd_to_send,
                        record_pipe,
                        transform=_count_and_hash)

        expected_hash = hasher.digest()
        expected_hex = bytes_to_hexstr(expected_hash)
        print(u"File sent.. waiting for confirmation", file=stderr)
        with self._timing.add("get ack") as t:
            ack_bytes = yield record_pipe.receive_record()
            record_pipe.close()
            ack = bytes_to_dict(ack_bytes)
            ok = ack.get(u"ack", u"")
            if ok != u"ok":
                t.detail(ack="failed")
                raise TransferError("Transfer failed (remote says: %r)" % ack)
            if u"sha256" in ack:
                if ack[u"sha256"] != expected_hex:
                    t.detail(datahash="failed")
                    raise TransferError("Transfer failed (bad remote hash)")
            print(u"Confirmation received. Transfer complete.", file=stderr)
            t.detail(ack="ok")
