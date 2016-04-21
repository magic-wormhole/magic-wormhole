from __future__ import print_function
import os, sys, io, json, binascii, six, tempfile, zipfile
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from ..errors import TransferError
from .progress import ProgressPrinter
from ..twisted.transcribe import Wormhole, WrongPasswordError
from ..twisted.transit import TransitSender

APPID = u"lothar.com/wormhole/text-or-file-xfer"

def handle_zero(args):
    if args.zeromode:
        assert not args.code
        args.code = u"0-"

def build_other_command(args):
    other_cmd = "wormhole receive"
    if args.verify:
        other_cmd = "wormhole --verify receive"
    if args.zeromode:
        other_cmd += " -0"
    return other_cmd

def build_phase1_data(args):
    phase1 = {}

    text = args.text
    if text == "-":
        print(u"Reading text message from stdin..", file=args.stdout)
        text = sys.stdin.read()
    if not text and not args.what:
        text = six.moves.input("Text to send: ")

    if text is not None:
        print(u"Sending text message (%d bytes)" % len(text), file=args.stdout)
        phase1 = { "message": text }
        fd_to_send = None
        return phase1, fd_to_send

    what = os.path.join(args.cwd, args.what)
    what = what.rstrip(os.sep)
    if not os.path.exists(what):
        raise TransferError("Cannot send: no file/directory named '%s'" %
                            args.what)
    basename = os.path.basename(what)

    if os.path.isfile(what):
        # we're sending a file
        filesize = os.stat(what).st_size
        phase1["file"] = {
            "filename": basename,
            "filesize": filesize,
            }
        print(u"Sending %d byte file named '%s'" % (filesize, basename),
              file=args.stdout)
        fd_to_send = open(what, "rb")
        return phase1, fd_to_send

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
        phase1["directory"] = {
            "mode": "zipfile/deflated",
            "dirname": basename,
            "zipsize": filesize,
            "numbytes": num_bytes,
            "numfiles": num_files,
            }
        print(u"Sending directory (%d bytes compressed) named '%s'"
              % (filesize, basename), file=args.stdout)
        return phase1, fd_to_send

    raise TypeError("'%s' is neither file nor directory" % args.what)

@inlineCallbacks
def send_twisted(args, reactor=reactor):
    assert isinstance(args.relay_url, type(u""))
    handle_zero(args)
    # TODO: parallelize the roundtrip that allocates the channel with the
    # (blocking) local IO (file os.stat, zipfile generation)
    phase1, fd_to_send = build_phase1_data(args)
    other_cmd = build_other_command(args)
    print(u"On the other computer, please run: %s" % other_cmd,
          file=args.stdout)

    tor_manager = None
    if args.tor:
        from ..twisted.tor_manager import TorManager
        tor_manager = TorManager(reactor, timing=args.timing)
        # For now, block everything until Tor has started. Soon: launch tor
        # in parallel with everything else, make sure the TorManager can
        # lazy-provide an endpoint, and overlap the startup process with the
        # user handing off the wormhole code
        yield tor_manager.start()

    w = Wormhole(APPID, args.relay_url, tor_manager, timing=args.timing,
                 reactor=reactor)

    if fd_to_send:
        transit_sender = TransitSender(args.transit_helper,
                                       no_listen=args.no_listen,
                                       tor_manager=tor_manager,
                                       reactor=reactor,
                                       timing=args.timing)
        phase1["transit"] = transit_data = {}
        transit_data["relay_connection_hints"] = transit_sender.get_relay_hints()
        direct_hints = yield transit_sender.get_direct_hints()
        transit_data["direct_connection_hints"] = direct_hints

    if args.code:
        w.set_code(args.code)
        code = args.code
    else:
        code = yield w.get_code(args.code_length)

    if not args.zeromode:
        print(u"Wormhole code is: %s" % code, file=args.stdout)
    print(u"", file=args.stdout)

    # get the verifier, because that also lets us derive the transit key,
    # which we want to set before revealing the connection hints to the far
    # side, so we'll be ready for them when they connect
    verifier_bytes = yield w.get_verifier()
    verifier = binascii.hexlify(verifier_bytes).decode("ascii")

    if args.verify:
        while True:
            ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                reject_data = json.dumps({"error": "verification rejected",
                                          }).encode("utf-8")
                yield w.send_data(reject_data)
                raise TransferError("verification rejected, abandoning transfer")
    if fd_to_send is not None:
        transit_key = w.derive_key(APPID+"/transit-key")
        transit_sender.set_transit_key(transit_key)

    my_phase1_bytes = json.dumps(phase1).encode("utf-8")
    yield w.send_data(my_phase1_bytes)

    try:
        them_phase1_bytes = yield w.get_data()
    except WrongPasswordError as e:
        raise TransferError(e.explain())

    them_phase1 = json.loads(them_phase1_bytes.decode("utf-8"))

    if fd_to_send is None:
        if them_phase1["message_ack"] == "ok":
            print(u"text message sent", file=args.stdout)
            yield w.close()
            returnValue(0) # terminates this function
        raise TransferError("error sending text: %r" % (them_phase1,))

    if "error" in them_phase1:
        raise TransferError("remote error, transfer abandoned: %s"
                            % them_phase1["error"])
    if them_phase1.get("file_ack") != "ok":
        raise TransferError("ambiguous response from remote, "
                            "transfer abandoned: %s" % (them_phase1,))
    tdata = them_phase1["transit"]
    yield w.close()
    yield _send_file_twisted(tdata, transit_sender, fd_to_send,
                             args.stdout, args.hide_progress, args.timing)
    returnValue(0)

class ProgressingFileSender(basic.FileSender):
    def __init__(self, filesize, stdout):
        self._sent = 0
        self._progress = ProgressPrinter(filesize, stdout)
        self._progress.start()
    def progress(self, data):
        self._sent += len(data)
        self._progress.update(self._sent)
        return data
    def beginFileTransfer(self, file, consumer):
        d = basic.FileSender.beginFileTransfer(self, file, consumer,
                                               self.progress)
        d.addCallback(self.done)
        return d
    def done(self, res):
        self._progress.finish()
        return res

@inlineCallbacks
def _send_file_twisted(tdata, transit_sender, fd_to_send,
                       stdout, hide_progress, timing):
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])

    fd_to_send.seek(0,2)
    filesize = fd_to_send.tell()
    fd_to_send.seek(0,0)
    progress_stdout = stdout
    if hide_progress:
        progress_stdout = io.StringIO()

    record_pipe = yield transit_sender.connect()
    # record_pipe should implement IConsumer, chunks are just records
    print(u"Sending (%s).." % record_pipe.describe(), file=stdout)
    pfs = ProgressingFileSender(filesize, progress_stdout)
    _start = timing.add_event("tx file")
    yield pfs.beginFileTransfer(fd_to_send, record_pipe)
    timing.finish_event(_start)

    print(u"File sent.. waiting for confirmation", file=stdout)
    _start = timing.add_event("get ack")
    ack = yield record_pipe.receive_record()
    record_pipe.close()
    if ack != b"ok\n":
        timing.finish_event(_start, ack="failed")
        raise TransferError("Transfer failed (remote says: %r)" % ack)
    print(u"Confirmation received. Transfer complete.", file=stdout)
    timing.finish_event(_start, ack="ok")
