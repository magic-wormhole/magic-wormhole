from __future__ import print_function
import os, sys, six, tempfile, zipfile, hashlib
from tqdm import tqdm
from humanize import naturalsize
from twisted.python import log
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from ..errors import TransferError, WormholeClosedError
from ..wormhole import wormhole
from ..transit import TransitSender
from ..util import dict_to_bytes, bytes_to_dict, bytes_to_hexstr

APPID = u"lothar.com/wormhole/text-or-file-xfer"
VERIFY_TIMER = 1

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
        self._tor_manager = None
        self._timing = args.timing
        self._fd_to_send = None
        self._transit_sender = None

    @inlineCallbacks
    def go(self):
        assert isinstance(self._args.relay_url, type(u""))
        if self._args.tor:
            with self._timing.add("import", which="tor_manager"):
                from ..tor_manager import TorManager
            self._tor_manager = TorManager(reactor, timing=self._timing)
            # For now, block everything until Tor has started. Soon: launch
            # tor in parallel with everything else, make sure the TorManager
            # can lazy-provide an endpoint, and overlap the startup process
            # with the user handing off the wormhole code
            yield self._tor_manager.start()

        w = wormhole(self._args.appid or APPID, self._args.relay_url,
                     self._reactor, self._tor_manager,
                     timing=self._timing)
        d = self._go(w)
        d.addBoth(w.close) # must wait for ack from close()
        yield d

    def _send_data(self, data, w):
        data_bytes = dict_to_bytes(data)
        w.send(data_bytes)

    @inlineCallbacks
    def _go(self, w):
        # TODO: run the blocking zip-the-directory IO in a thread, let the
        # wormhole exchange happen in parallel
        offer, self._fd_to_send = self._build_offer()
        args = self._args

        other_cmd = "wormhole receive"
        if args.verify:
            other_cmd = "wormhole receive --verify"
        if args.zeromode:
            assert not args.code
            args.code = u"0-"
            other_cmd += " -0"

        print(u"On the other computer, please run: %s" % other_cmd,
              file=args.stderr)

        if args.code:
            w.set_code(args.code)
            code = args.code
        else:
            code = yield w.get_code(args.code_length)

        if not args.zeromode:
            print(u"Wormhole code is: %s" % code, file=args.stderr)
            # flush stderr so the code is displayed immediately
            args.stderr.flush()
        print(u"", file=args.stderr)

        yield w.establish_key()
        def on_slow_connection():
            print(u"Key established, waiting for confirmation...",
                  file=args.stderr)
        notify = self._reactor.callLater(VERIFY_TIMER, on_slow_connection)

        # TODO: don't stall on w.verify() unless they want it
        try:
            verifier_bytes = yield w.verify() # this may raise WrongPasswordError
        finally:
            if not notify.called:
                notify.cancel()

        if args.verify:
            verifier = bytes_to_hexstr(verifier_bytes)
            while True:
                ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
                if ok.lower() == "yes":
                    break
                if ok.lower() == "no":
                    err = "sender rejected verification check, abandoned transfer"
                    reject_data = dict_to_bytes({"error": err})
                    w.send(reject_data)
                    raise TransferError(err)

        if self._fd_to_send:
            ts = TransitSender(args.transit_helper,
                               no_listen=(not args.listen),
                               tor_manager=self._tor_manager,
                               reactor=self._reactor,
                               timing=self._timing)
            self._transit_sender = ts

            # for now, send this before the main offer
            sender_abilities = ts.get_connection_abilities()
            sender_hints = yield ts.get_connection_hints()
            sender_transit = {"abilities-v1": sender_abilities,
                              "hints-v1": sender_hints,
                              }
            self._send_data({u"transit": sender_transit}, w)

            # TODO: move this down below w.get()
            transit_key = w.derive_key(APPID+"/transit-key",
                                       ts.TRANSIT_KEY_LENGTH)
            ts.set_transit_key(transit_key)

        self._send_data({"offer": offer}, w)

        want_answer = True
        done = False

        while True:
            try:
                them_d_bytes = yield w.get()
            except WormholeClosedError:
                if done:
                    returnValue(None)
                raise TransferError("unexpected close")
            # TODO: get() fired, so now it's safe to use w.derive_key()
            them_d = bytes_to_dict(them_d_bytes)
            #print("GOT", them_d)
            recognized = False
            if u"error" in them_d:
                raise TransferError("remote error, transfer abandoned: %s"
                                    % them_d["error"])
            if u"transit" in them_d:
                recognized = True
                yield self._handle_transit(them_d[u"transit"])
            if u"answer" in them_d:
                recognized = True
                if not want_answer:
                    raise TransferError("duplicate answer")
                yield self._handle_answer(them_d[u"answer"])
                done = True
                returnValue(None)
            if not recognized:
                log.msg("unrecognized message %r" % (them_d,))

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
            print(u"Sending text message (%s)" % naturalsize(len(text)),
                  file=args.stderr)
            offer = { "message": text }
            fd_to_send = None
            return offer, fd_to_send

        what = os.path.join(args.cwd, args.what)
        what = what.rstrip(os.sep)
        if not os.path.exists(what):
            raise TransferError("Cannot send: no file/directory named '%s'" %
                                args.what)
        basename = os.path.basename(what)

        if os.path.isfile(what):
            # we're sending a file
            filesize = os.stat(what).st_size
            offer["file"] = {
                "filename": basename,
                "filesize": filesize,
                }
            print(u"Sending %s file named '%s'"
                  % (naturalsize(filesize), basename),
                  file=args.stderr)
            fd_to_send = open(what, "rb")
            return offer, fd_to_send

        if os.path.isdir(what):
            print(u"Building zipfile..", file=args.stderr)
            # We're sending a directory. Create a zipfile in a tempdir and
            # send that.
            fd_to_send = tempfile.SpooledTemporaryFile()
            num_files = 0
            num_bytes = 0
            tostrip = len(what.split(os.sep))
            with zipfile.ZipFile(fd_to_send, "w", zipfile.ZIP_DEFLATED) as zf:
                for path,dirs,files in os.walk(what):
                    # path always starts with args.what, then sometimes might
                    # have "/subdir" appended. We want the zipfile to contain
                    # "" or "subdir"
                    localpath = list(path.split(os.sep)[tostrip:])
                    for fn in files:
                        archivename = os.path.join(*tuple(localpath+[fn]))
                        localfilename = os.path.join(path, fn)
                        zf.write(localfilename, archivename)
                        num_bytes += os.stat(localfilename).st_size
                        num_files += 1
            fd_to_send.seek(0,2)
            filesize = fd_to_send.tell()
            fd_to_send.seek(0,0)
            offer["directory"] = {
                "mode": "zipfile/deflated",
                "dirname": basename,
                "zipsize": filesize,
                "numbytes": num_bytes,
                "numfiles": num_files,
                }
            print(u"Sending directory (%s compressed) named '%s'"
                  % (naturalsize(filesize), basename), file=args.stderr)
            return offer, fd_to_send

        raise TypeError("'%s' is neither file nor directory" % args.what)

    @inlineCallbacks
    def _handle_answer(self, them_answer):
        if self._fd_to_send is None:
            if them_answer["message_ack"] == "ok":
                print(u"text message sent", file=self._args.stderr)
                returnValue(None) # terminates this function
            raise TransferError("error sending text: %r" % (them_answer,))

        if them_answer.get("file_ack") != "ok":
            raise TransferError("ambiguous response from remote, "
                                "transfer abandoned: %s" % (them_answer,))

        yield self._send_file()


    @inlineCallbacks
    def _send_file(self):
        ts = self._transit_sender

        self._fd_to_send.seek(0,2)
        filesize = self._fd_to_send.tell()
        self._fd_to_send.seek(0,0)

        record_pipe = yield ts.connect()
        self._timing.add("transit connected")
        # record_pipe should implement IConsumer, chunks are just records
        stderr = self._args.stderr
        print(u"Sending (%s).." % record_pipe.describe(), file=stderr)

        hasher = hashlib.sha256()
        progress = tqdm(file=stderr, disable=self._args.hide_progress,
                        unit="B", unit_scale=True,
                        total=filesize)
        def _count_and_hash(data):
            hasher.update(data)
            progress.update(len(data))
            return data
        fs = basic.FileSender()

        with self._timing.add("tx file"):
            with progress:
                yield fs.beginFileTransfer(self._fd_to_send, record_pipe,
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
