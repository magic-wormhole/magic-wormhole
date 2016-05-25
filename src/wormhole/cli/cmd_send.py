from __future__ import print_function
import os, sys, json, binascii, six, tempfile, zipfile
from tqdm import tqdm
from twisted.python import log
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from ..errors import TransferError, WormholeClosedError
from ..wormhole import wormhole
from ..transit import TransitSender

APPID = u"lothar.com/wormhole/text-or-file-xfer"

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

        w = wormhole(APPID, self._args.relay_url,
                     self._reactor, self._tor_manager,
                     timing=self._timing)
        d = self._go(w)
        d.addBoth(w.close)
        yield d

    @inlineCallbacks
    def _go(self, w):
        # TODO: run the blocking zip-the-directory IO in a thread, let the
        # wormhole exchange happen in parallel
        offer, self._fd_to_send = self._build_offer()
        args = self._args

        other_cmd = "wormhole receive"
        if args.verify:
            other_cmd = "wormhole --verify receive"
        if args.zeromode:
            assert not args.code
            args.code = u"0-"
            other_cmd += " -0"

        print(u"On the other computer, please run: %s" % other_cmd,
              file=args.stdout)

        if args.code:
            w.set_code(args.code)
            code = args.code
        else:
            code = yield w.get_code(args.code_length)

        if not args.zeromode:
            print(u"Wormhole code is: %s" % code, file=args.stdout)
        print(u"", file=args.stdout)

        # TODO: don't stall on w.verify() unless they want it
        verifier_bytes = yield w.verify() # this may raise WrongPasswordError
        if args.verify:
            verifier = binascii.hexlify(verifier_bytes).decode("ascii")
            while True:
                ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
                if ok.lower() == "yes":
                    break
                if ok.lower() == "no":
                    err = "sender rejected verification check, abandoned transfer"
                    reject_data = json.dumps({"error": err}).encode("utf-8")
                    w.send(reject_data)
                    raise TransferError(err)

        if self._fd_to_send:
            ts = TransitSender(args.transit_helper,
                               no_listen=args.no_listen,
                               tor_manager=self._tor_manager,
                               reactor=self._reactor,
                               timing=self._timing)
            self._transit_sender = ts
            offer["transit"] = transit_data = {}
            transit_data["relay_connection_hints"] = ts.get_relay_hints()
            direct_hints = yield ts.get_direct_hints()
            transit_data["direct_connection_hints"] = direct_hints

            # TODO: move this down below w.get()
            transit_key = w.derive_key(APPID+"/transit-key",
                                       ts.TRANSIT_KEY_LENGTH)
            ts.set_transit_key(transit_key)

        my_offer_bytes = json.dumps({"offer": offer}).encode("utf-8")
        w.send(my_offer_bytes)

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
            them_d = json.loads(them_d_bytes.decode("utf-8"))
            if u"answer" in them_d:
                if not want_answer:
                    raise TransferError("duplicate answer")
                them_answer = them_d[u"answer"]
                yield self._handle_answer(them_answer)
                done = True
                returnValue(None)
            log.msg("unrecognized message %r" % (them_d,))

    def _build_offer(self):
        offer = {}

        args = self._args
        text = args.text
        if text == "-":
            print(u"Reading text message from stdin..", file=args.stdout)
            text = sys.stdin.read()
        if not text and not args.what:
            text = six.moves.input("Text to send: ")

        if text is not None:
            print(u"Sending text message (%d bytes)" % len(text),
                  file=args.stdout)
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
            print(u"Sending %d byte file named '%s'" % (filesize, basename),
                  file=args.stdout)
            fd_to_send = open(what, "rb")
            return offer, fd_to_send

        if os.path.isdir(what):
            print(u"Building zipfile..", file=args.stdout)
            # We're sending a directory. Create a zipfile in a tempdir and
            # send that.
            fd_to_send = tempfile.SpooledTemporaryFile()
            # TODO: I think ZIP_DEFLATED means compressed.. check it
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
            print(u"Sending directory (%d bytes compressed) named '%s'"
                  % (filesize, basename), file=args.stdout)
            return offer, fd_to_send

        raise TypeError("'%s' is neither file nor directory" % args.what)

    @inlineCallbacks
    def _handle_answer(self, them_answer):
        if self._fd_to_send is None:
            if them_answer["message_ack"] == "ok":
                print(u"text message sent", file=self._args.stdout)
                returnValue(None) # terminates this function
            raise TransferError("error sending text: %r" % (them_answer,))

        if "error" in them_answer:
            raise TransferError("remote error, transfer abandoned: %s"
                                % them_answer["error"])
        if them_answer.get("file_ack") != "ok":
            raise TransferError("ambiguous response from remote, "
                                "transfer abandoned: %s" % (them_answer,))

        tdata = them_answer["transit"]
        yield self._send_file_twisted(tdata)


    @inlineCallbacks
    def _send_file_twisted(self, tdata):
        ts = self._transit_sender
        ts.add_their_direct_hints(tdata["direct_connection_hints"])
        ts.add_their_relay_hints(tdata["relay_connection_hints"])

        self._fd_to_send.seek(0,2)
        filesize = self._fd_to_send.tell()
        self._fd_to_send.seek(0,0)

        record_pipe = yield ts.connect()
        self._timing.add("transit connected")
        # record_pipe should implement IConsumer, chunks are just records
        stdout = self._args.stdout
        print(u"Sending (%s).." % record_pipe.describe(), file=stdout)

        progress = tqdm(file=stdout, disable=self._args.hide_progress,
                        unit="B", unit_scale=True,
                        total=filesize)
        def _count(data):
            progress.update(len(data))
            return data
        fs = basic.FileSender()

        with self._timing.add("tx file"):
            with progress:
                yield fs.beginFileTransfer(self._fd_to_send, record_pipe,
                                           transform=_count)

        print(u"File sent.. waiting for confirmation", file=stdout)
        with self._timing.add("get ack") as t:
            ack = yield record_pipe.receive_record()
            record_pipe.close()
            if ack != b"ok\n":
                t.detail(ack="failed")
                raise TransferError("Transfer failed (remote says: %r)" % ack)
            print(u"Confirmation received. Transfer complete.", file=stdout)
            t.detail(ack="ok")
