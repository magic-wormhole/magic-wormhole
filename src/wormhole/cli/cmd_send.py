import errno
import hashlib
import os
import sys

import stat

from humanize import naturalsize
from qrcode import QRCode
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.protocols import basic
from twisted.python import log
from wormhole import __version__, create

from ..errors import TransferError, UnsendableFileError
from .._status import WormholeStatus, ConsumedCode
from ..transit import TransitSender
from ..util import bytes_to_dict, bytes_to_hexstr, dict_to_bytes
from .welcome import handle_welcome

from iterableio import open_iterable
from zipstream.ng import ZipStream, walk

APPID = "lothar.com/wormhole/text-or-file-xfer"
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
        self._status = WormholeStatus()

    @inlineCallbacks
    def go(self):
        assert isinstance(self._args.relay_url, str)
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
            timing=self._timing,
            on_status_update=self._on_status,
        )
        if self._args.debug_state:
            w.debug_set_trace("send", which=" ".join(self._args.debug_state), file=self._args.stdout)
        d = self._go(w)

        # if we succeed, we should close and return the w.close results
        # (which might be an error)
        @inlineCallbacks
        def _good(res):
            yield w.close()  # wait for ack
            return res

        # if we raise an error, we should close and then return the original
        # error (the close might give us an error, but it isn't as important
        # as the original one)
        @inlineCallbacks
        def _bad(f):
            try:
                yield w.close()  # might be an error too
            except Exception:
                pass
            return f

        d.addCallbacks(_good, _bad)
        yield d

    def _send_data(self, data, w):
        data_bytes = dict_to_bytes(data)
        w.send_message(data_bytes)

    def _on_status(self, status):
        if not isinstance(self._status.code, ConsumedCode) and isinstance(status.code, ConsumedCode):
            print("Note: code has been consumed and can no longer be used.", file=self._args.stdout)
        self._status = status

    @inlineCallbacks
    def _go(self, w):
        welcome = yield w.get_welcome()
        handle_welcome(welcome, self._args.relay_url, __version__,
                       self._args.stderr)

        # TODO: run the blocking zip-the-directory IO in a thread, let the
        # wormhole exchange happen in parallel
        offer, self._fd_to_send = self._build_offer()
        args = self._args

        other_cmd = "wormhole receive"
        if args.verify:
            other_cmd = "wormhole receive --verify"
        if args.zeromode:
            assert not args.code
            args.code = "0-"
            other_cmd += " -0"

        if args.code:
            w.set_code(args.code)
        else:
            w.allocate_code(args.code_length)

        code = yield w.get_code()
        if not args.zeromode:
            print(f"Wormhole code is: {code}", file=args.stderr)
            other_cmd += " " + code

        if not args.zeromode and args.qr:
            qr = QRCode(border=1)
            qr.add_data(f"wormhole-transfer:{code}")
            # Use TTY colors if available, otherwise use ASCII
            if args.stderr.isatty():
                qr.print_ascii(out=args.stderr, tty=True, invert=False)
            else:
                qr.print_ascii(out=args.stderr, tty=False, invert=False)

        print("On the other computer, please run:", file=args.stderr)
        print("", file=args.stderr)
        print(other_cmd, file=args.stderr)
        print("", file=args.stderr)
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
                "Key established, waiting for confirmation...",
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
            # check_verifier() does a blocking call to input(), so stall for
            # a moment to let any outbound messages get written into the
            # kernel. At this point, we're sitting in a callback of
            # get_verifier(), which is triggered by receipt of the other
            # side's VERSION message. But we might have gotten both the PAKE
            # and the VERSION message in the same turn, and our outbound
            # VERSION message (triggered by receipt of their PAKE) is still
            # in Twisted's transmit queue. If we don't wait a moment, it will
            # be stuck there until `input()` returns, and the receiver won't
            # be able to compute a Verifier for the users to compare. #349
            # has more details
            d = Deferred()
            reactor.callLater(0.001, d.callback, None)
            yield d
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
            self._send_data({"transit": sender_transit}, w)

            # When I made it possible to override APPID with a CLI argument
            # (issue #113), I forgot to also change this w.derive_key()
            # (issue #339). We're stuck with it now. Use a local constant to
            # make this clear.
            BUG339_APPID = "lothar.com/wormhole/text-or-file-xfer"

            # TODO: move this down below w.get_message()
            transit_key = w.derive_key(BUG339_APPID + "/transit-key",
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
            if "error" in them_d:
                raise TransferError(
                    f"remote error, transfer abandoned: {them_d['error']}")
            if "transit" in them_d:
                recognized = True
                yield self._handle_transit(them_d["transit"])
            if "answer" in them_d:
                recognized = True
                if not want_answer:
                    raise TransferError("duplicate answer")
                want_answer = True
                yield self._handle_answer(them_d["answer"])
                return None
            if not recognized:
                log.msg(f"unrecognized message {them_d!r}")

    def _check_verifier(self, w, verifier_bytes):
        verifier = bytes_to_hexstr(verifier_bytes)
        while True:
            ok = input(f"Verifier {verifier}. ok? (yes/no): ")
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
            print("Reading text message from stdin..", file=args.stderr)
            text = sys.stdin.read()
        if not text and not args.what:
            text = input("Text to send: ")

        if text is not None:
            print(
                f"Sending text message ({naturalsize(len(text))})",
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
                f"Cannot send: no file/directory named '{args.what}'")

        if os.path.isfile(what):
            # we're sending a file
            filesize = os.stat(what).st_size
            offer["file"] = {
                "filename": basename,
                "filesize": filesize,
            }
            print(
                f"Sending {naturalsize(filesize)} file named '{basename}'",
                file=args.stderr)
            fd_to_send = open(what, "rb")
            return offer, fd_to_send

        if os.path.isdir(what):
            print("Building zipfile..", file=args.stderr)
            # We're sending a directory, stream it as a zipfile

            zs = ZipStream(sized=True)
            for filepath in walk(what, preserve_empty=True, followlinks=True):
                try:
                    if not os.access(filepath, os.R_OK):
                        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), filepath)
                    zs.add_path(
                        filepath,
                        arcname=os.path.relpath(filepath, what),
                        recurse=False,
                    )
                except OSError as e:
                    errmsg = f"{filepath}: {e.strerror}"
                    if not self._args.ignore_unsendable_files:
                        raise UnsendableFileError(errmsg)
                    print(
                        f"{errmsg} (ignoring error)",
                        file=args.stderr
                    )

            filesizes = [x["size"] for x in zs.info_list() if not x["is_dir"]]
            filesize = len(zs)
            offer["directory"] = {
                "mode": "zipfile/deflated",
                "dirname": basename,
                "zipsize": filesize,
                "numbytes": sum(filesizes),
                "numfiles": len(filesizes),
            }
            print(
                "Sending directory (%s compressed) named '%s'" %
                (naturalsize(filesize), basename),
                file=args.stderr)
            return offer, zs

        if stat.S_ISBLK(os.stat(what).st_mode):
            fd_to_send = open(what, "rb")
            filesize = fd_to_send.seek(0, 2)

            offer["file"] = {
                "filename": basename,
                "filesize": filesize,
            }
            print(
                f"Sending {naturalsize(filesize)} block device named '{basename}'",
                file=args.stderr)

            fd_to_send.seek(0)
            return offer, fd_to_send

        raise TypeError(f"'{args.what}' is neither file nor directory")

    @inlineCallbacks
    def _handle_answer(self, them_answer):
        if self._fd_to_send is None:
            if them_answer["message_ack"] == "ok":
                print("text message sent", file=self._args.stderr)
                return None
            raise TransferError(f"error sending text: {them_answer!r}")

        if them_answer.get("file_ack") != "ok":
            raise TransferError("ambiguous response from remote, "
                                "transfer abandoned: %s" % (them_answer, ))

        yield self._send_file()

    @inlineCallbacks
    def _send_file(self):
        ts = self._transit_sender

        if isinstance(self._fd_to_send, ZipStream):
            filesize = len(self._fd_to_send)
            self._fd_to_send = open_iterable(self._fd_to_send, "rb")
        else:
            self._fd_to_send.seek(0, 2)
            filesize = self._fd_to_send.tell()
            self._fd_to_send.seek(0, 0)

        record_pipe = yield ts.connect()
        self._timing.add("transit connected")
        # record_pipe should implement IConsumer, chunks are just records
        stderr = self._args.stderr
        print(f"Sending ({record_pipe.describe()})..", file=stderr)

        hasher = hashlib.sha256()
        progress = tqdm(
            file=stderr,
            disable=self._args.hide_progress,
            unit="B",
            unit_scale=True,
            dynamic_ncols=True,
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
        print("File sent.. waiting for confirmation", file=stderr)
        with self._timing.add("get ack") as t:
            ack_bytes = yield record_pipe.receive_record()
            record_pipe.close()
            ack = bytes_to_dict(ack_bytes)
            ok = ack.get("ack", "")
            if ok != "ok":
                t.detail(ack="failed")
                raise TransferError(f"Transfer failed (remote says: {ack!r})")
            if "sha256" in ack:
                if ack["sha256"] != expected_hex:
                    t.detail(datahash="failed")
                    raise TransferError("Transfer failed (bad remote hash)")
            print("Confirmation received. Transfer complete.", file=stderr)
            t.detail(ack="ok")
