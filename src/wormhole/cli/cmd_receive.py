from __future__ import print_function

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile

import six
from humanize import naturalsize
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log
from wormhole import __version__, create, input_with_completion

from ..errors import TransferError
from ..transit import TransitReceiver
from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    estimate_free_space)
from .welcome import handle_welcome

APPID = u"lothar.com/wormhole/text-or-file-xfer"

KEY_TIMER = float(os.environ.get("_MAGIC_WORMHOLE_TEST_KEY_TIMER", 1.0))
VERIFY_TIMER = float(os.environ.get("_MAGIC_WORMHOLE_TEST_VERIFY_TIMER", 1.0))


class RespondError(Exception):
    def __init__(self, response):
        self.response = response


class TransferRejectedError(RespondError):
    def __init__(self):
        RespondError.__init__(self, "transfer rejected")


def receive(args, reactor=reactor, _debug_stash_wormhole=None):
    """I implement 'wormhole receive'. I return a Deferred that fires with
    None (for success), or signals one of the following errors:
    * WrongPasswordError: the two sides didn't use matching passwords
    * Timeout: something didn't happen fast enough for our tastes
    * TransferError: the sender rejected the transfer: verifier mismatch
    * any other error: something unexpected happened
    """
    r = Receiver(args, reactor)
    d = r.go()
    if _debug_stash_wormhole is not None:
        _debug_stash_wormhole.append(r._w)
    return d


class Receiver:
    def __init__(self, args, reactor=reactor):
        assert isinstance(args.relay_url, type(u""))
        self.args = args
        self._reactor = reactor
        self._tor = None
        self._transit_receiver = None

    def _msg(self, *args, **kwargs):
        print(*args, file=self.args.stderr, **kwargs)

    @inlineCallbacks
    def go(self):
        if self.args.tor:
            with self.args.timing.add("import", which="tor_manager"):
                from ..tor_manager import get_tor
            # For now, block everything until Tor has started. Soon: launch
            # tor in parallel with everything else, make sure the Tor object
            # can lazy-provide an endpoint, and overlap the startup process
            # with the user handing off the wormhole code
            self._tor = yield get_tor(
                self._reactor,
                self.args.launch_tor,
                self.args.tor_control_port,
                timing=self.args.timing)

        w = create(
            self.args.appid or APPID,
            self.args.relay_url,
            self._reactor,
            tor=self._tor,
            timing=self.args.timing)
        self._w = w  # so tests can wait on events too

        # I wanted to do this instead:
        #
        #    try:
        #        yield self._go(w, tor)
        #    finally:
        #        yield w.close()
        #
        # but when _go had a UsageError, the stacktrace was always displayed
        # as coming from the "yield self._go" line, which wasn't very useful
        # for tracking it down.
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

    @inlineCallbacks
    def _go(self, w):
        welcome = yield w.get_welcome()
        handle_welcome(welcome, self.args.relay_url, __version__,
                       self.args.stderr)

        yield self._handle_code(w)

        def on_slow_key():
            print(u"Waiting for sender...", file=self.args.stderr)

        notify = self._reactor.callLater(KEY_TIMER, on_slow_key)
        try:
            # We wait here until we connect to the server and see the senders
            # PAKE message. If we used set_code() in the "human-selected
            # offline codes" mode, then the sender might not have even
            # started yet, so we might be sitting here for a while. Because
            # of that possibility, it's probably not appropriate to give up
            # automatically after some timeout. The user can express their
            # impatience by quitting the program with control-C.
            yield w.get_unverified_key()
        finally:
            if not notify.called:
                notify.cancel()

        def on_slow_verification():
            print(
                u"Key established, waiting for confirmation...",
                file=self.args.stderr)

        notify = self._reactor.callLater(VERIFY_TIMER, on_slow_verification)
        try:
            # We wait here until we've seen their VERSION message (which they
            # send after seeing our PAKE message, and has the side-effect of
            # verifying that we both share the same key). There is a
            # round-trip between these two events, and we could experience a
            # significant delay here if:
            # * the relay server is being restarted
            # * the network is very slow
            # * the sender is very slow
            # * the sender has quit (in which case we may wait forever)

            # It would be reasonable to give up after waiting here for too
            # long.
            verifier_bytes = yield w.get_verifier()
        finally:
            if not notify.called:
                notify.cancel()
        self._show_verifier(verifier_bytes)

        want_offer = True

        while True:
            them_d = yield self._get_data(w)
            # print("GOT", them_d)
            recognized = False
            if u"transit" in them_d:
                recognized = True
                yield self._parse_transit(them_d[u"transit"], w)
            if u"offer" in them_d:
                recognized = True
                if not want_offer:
                    raise TransferError("duplicate offer")
                want_offer = False
                try:
                    yield self._parse_offer(them_d[u"offer"], w)
                except RespondError as r:
                    self._send_data({"error": r.response}, w)
                    raise TransferError(r.response)
                returnValue(None)
            if not recognized:
                log.msg("unrecognized message %r" % (them_d, ))

    def _send_data(self, data, w):
        data_bytes = dict_to_bytes(data)
        w.send_message(data_bytes)

    @inlineCallbacks
    def _get_data(self, w):
        # this may raise WrongPasswordError
        them_bytes = yield w.get_message()
        them_d = bytes_to_dict(them_bytes)
        if "error" in them_d:
            raise TransferError(them_d["error"])
        returnValue(them_d)

    @inlineCallbacks
    def _handle_code(self, w):
        code = self.args.code
        if self.args.zeromode:
            assert not code
            code = u"0-"
        if code:
            w.set_code(code)
        else:
            prompt = "Enter receive wormhole code: "
            used_completion = yield input_with_completion(
                prompt, w.input_code(), self._reactor)
            if not used_completion:
                print(
                    " (note: you can use <Tab> to complete words)",
                    file=self.args.stderr)
        yield w.get_code()

    def _show_verifier(self, verifier_bytes):
        verifier_hex = bytes_to_hexstr(verifier_bytes)
        if self.args.verify:
            self._msg(u"Verifier %s." % verifier_hex)

    @inlineCallbacks
    def _parse_transit(self, sender_transit, w):
        if self._transit_receiver:
            # TODO: accept multiple messages, add the additional hints to the
            # existing TransitReceiver
            return
        yield self._build_transit(w, sender_transit)

    @inlineCallbacks
    def _build_transit(self, w, sender_transit):
        tr = TransitReceiver(
            self.args.transit_helper,
            no_listen=(not self.args.listen),
            tor=self._tor,
            reactor=self._reactor,
            timing=self.args.timing)
        self._transit_receiver = tr
        transit_key = w.derive_key(APPID + u"/transit-key",
                                   tr.TRANSIT_KEY_LENGTH)
        tr.set_transit_key(transit_key)

        tr.add_connection_hints(sender_transit.get("hints-v1", []))
        receiver_abilities = tr.get_connection_abilities()
        receiver_hints = yield tr.get_connection_hints()
        receiver_transit = {
            "abilities-v1": receiver_abilities,
            "hints-v1": receiver_hints,
        }
        self._send_data({u"transit": receiver_transit}, w)
        # TODO: send more hints as the TransitReceiver produces them

    @inlineCallbacks
    def _parse_offer(self, them_d, w):
        if "message" in them_d:
            self._handle_text(them_d, w)
            returnValue(None)
        # transit will be created by this point, but not connected
        if "file" in them_d:
            f = self._handle_file(them_d)
            self._send_permission(w)
            rp = yield self._establish_transit()
            datahash = yield self._transfer_data(rp, f)
            self._write_file(f)
            yield self._close_transit(rp, datahash)
        elif "directory" in them_d:
            f = self._handle_directory(them_d)
            self._send_permission(w)
            rp = yield self._establish_transit()
            datahash = yield self._transfer_data(rp, f)
            self._write_directory(f)
            yield self._close_transit(rp, datahash)
        else:
            self._msg(u"I don't know what they're offering\n")
            self._msg(u"Offer details: %r" % (them_d, ))
            raise RespondError("unknown offer type")

    def _handle_text(self, them_d, w):
        # we're receiving a text message
        self.args.timing.add("print")
        print(them_d["message"], file=self.args.stdout)
        self._send_data({"answer": {"message_ack": "ok"}}, w)

    def _handle_file(self, them_d):
        file_data = them_d["file"]
        self.abs_destname = self._decide_destname("file",
                                                  file_data["filename"])
        self.xfersize = file_data["filesize"]
        free = estimate_free_space(self.abs_destname)
        if free is not None and free < self.xfersize:
            self._msg(u"Error: insufficient free space (%sB) for file (%sB)" %
                      (free, self.xfersize))
            raise TransferRejectedError()

        self._msg(u"Receiving file (%s) into: %s" %
                  (naturalsize(self.xfersize),
                   os.path.basename(self.abs_destname)))
        self._ask_permission()
        tmp_destname = self.abs_destname + ".tmp"
        return open(tmp_destname, "wb")

    def _handle_directory(self, them_d):
        file_data = them_d["directory"]
        zipmode = file_data["mode"]
        if zipmode != "zipfile/deflated":
            self._msg(u"Error: unknown directory-transfer mode '%s'" %
                      (zipmode, ))
            raise RespondError("unknown mode")
        self.abs_destname = self._decide_destname("directory",
                                                  file_data["dirname"])
        self.xfersize = file_data["zipsize"]
        free = estimate_free_space(self.abs_destname)
        if free is not None and free < file_data["numbytes"]:
            self._msg(
                u"Error: insufficient free space (%sB) for directory (%sB)" %
                (free, file_data["numbytes"]))
            raise TransferRejectedError()

        self._msg(u"Receiving directory (%s) into: %s/" %
                  (naturalsize(self.xfersize),
                   os.path.basename(self.abs_destname)))
        self._msg(u"%d files, %s (uncompressed)" %
                  (file_data["numfiles"], naturalsize(file_data["numbytes"])))
        self._ask_permission()
        f = tempfile.SpooledTemporaryFile()
        # workaround for https://bugs.python.org/issue26175 (STF doesn't
        # fully implement IOBase abstract class), which breaks the new
        # zipfile in py3.7.0 that expects .seekable
        if not hasattr(f, "seekable"):
            # AFAICT all the filetypes that STF wraps can seek
            f.seekable = lambda: True
        return f

    def _decide_destname(self, mode, destname):
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        destname = os.path.basename(destname)
        if self.args.output_file:
            destname = self.args.output_file  # override
        abs_destname = os.path.abspath(os.path.join(self.args.cwd, destname))

        # get confirmation from the user before writing to the local directory
        if os.path.exists(abs_destname):
            if self.args.output_file:  # overwrite is intentional
                self._msg(u"Overwriting '%s'" % destname)
                if self.args.accept_file:
                    self._remove_existing(abs_destname)
            else:
                self._msg(
                    u"Error: refusing to overwrite existing '%s'" % destname)
                raise TransferRejectedError()
        return abs_destname

    def _remove_existing(self, path):
        if os.path.isfile(path):
            os.remove(path)
        if os.path.isdir(path):
            shutil.rmtree(path)

    def _ask_permission(self):
        with self.args.timing.add("permission", waiting="user") as t:
            while True and not self.args.accept_file:
                ok = six.moves.input("ok? (y/N): ")
                if ok.lower().startswith("y"):
                    if os.path.exists(self.abs_destname):
                        self._remove_existing(self.abs_destname)
                    break
                print(u"transfer rejected", file=sys.stderr)
                t.detail(answer="no")
                raise TransferRejectedError()
            t.detail(answer="yes")

    def _send_permission(self, w):
        self._send_data({"answer": {"file_ack": "ok"}}, w)

    @inlineCallbacks
    def _establish_transit(self):
        record_pipe = yield self._transit_receiver.connect()
        self.args.timing.add("transit connected")
        returnValue(record_pipe)

    @inlineCallbacks
    def _transfer_data(self, record_pipe, f):
        # now receive the rest of the owl
        self._msg(u"Receiving (%s).." % record_pipe.describe())

        with self.args.timing.add("rx file"):
            progress = tqdm(
                file=self.args.stderr,
                disable=self.args.hide_progress,
                unit="B",
                unit_scale=True,
                total=self.xfersize)
            hasher = hashlib.sha256()
            with progress:
                received = yield record_pipe.writeToFile(
                    f, self.xfersize, progress.update, hasher.update)
            datahash = hasher.digest()

        # except TransitError
        if received < self.xfersize:
            self._msg()
            self._msg(u"Connection dropped before full file received")
            self._msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
            raise TransferError("Connection dropped before full file received")
        assert received == self.xfersize
        returnValue(datahash)

    def _write_file(self, f):
        tmp_name = f.name
        f.close()
        os.rename(tmp_name, self.abs_destname)
        self._msg(u"Received file written to %s" % os.path.basename(
            self.abs_destname))

    def _extract_file(self, zf, info, extract_dir):
        """
        the zipfile module does not restore file permissions
        so we'll do it manually
        """
        out_path = os.path.join(extract_dir, info.filename)
        out_path = os.path.abspath(out_path)
        if not out_path.startswith(extract_dir):
            raise ValueError(
                "malicious zipfile, %s outside of extract_dir %s" %
                (info.filename, extract_dir))

        zf.extract(info.filename, path=extract_dir)

        # not sure why zipfiles store the perms 16 bits away but they do
        perm = info.external_attr >> 16
        os.chmod(out_path, perm)

    def _write_directory(self, f):

        self._msg(u"Unpacking zipfile..")
        with self.args.timing.add("unpack zip"):
            with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
                for info in zf.infolist():
                    self._extract_file(zf, info, self.abs_destname)

            self._msg(u"Received files written to %s/" % os.path.basename(
                self.abs_destname))
            f.close()

    @inlineCallbacks
    def _close_transit(self, record_pipe, datahash):
        datahash_hex = bytes_to_hexstr(datahash)
        ack = {u"ack": u"ok", u"sha256": datahash_hex}
        ack_bytes = dict_to_bytes(ack)
        with self.args.timing.add("send ack"):
            yield record_pipe.send_record(ack_bytes)
            yield record_pipe.close()
