from __future__ import print_function
import io, json, binascii, six
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from ..errors import TransferError
from .progress import ProgressPrinter
from ..twisted.transcribe import Wormhole, WrongPasswordError
from ..twisted.transit import TransitSender
from .send_common import (APPID, handle_zero, build_other_command,
                          build_phase1_data)

def send_twisted_sync(args):
    d = send_twisted(args)
    # try to use twisted.internet.task.react(f) here (but it calls sys.exit
    # directly)
    rc = []
    def _done(res):
        rc.extend([True, res])
        reactor.stop()
    def _err(f):
        rc.extend([False, f.value])
        reactor.stop()
    d.addCallbacks(_done, _err)
    reactor.run()
    if rc[0]:
        return rc[1]
    raise rc[1]

@inlineCallbacks
def send_twisted(args):
    assert isinstance(args.relay_url, type(u""))
    handle_zero(args)
    phase1, fd_to_send = build_phase1_data(args)
    other_cmd = build_other_command(args)
    print(u"On the other computer, please run: %s" % other_cmd,
          file=args.stdout)

    w = Wormhole(APPID, args.relay_url)

    if fd_to_send:
        transit_sender = TransitSender(args.transit_helper)
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
                             args.stdout, args.hide_progress)
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
                       stdout, hide_progress):
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])

    fd_to_send.seek(0,2)
    filesize = fd_to_send.tell()
    fd_to_send.seek(0,0)
    progress_stdout = stdout
    if hide_progress:
        progress_stdout = io.StringIO()
    pfs = ProgressingFileSender(filesize, progress_stdout)

    record_pipe = yield transit_sender.connect()
    # record_pipe should implement IConsumer, chunks are just records
    print(u"Sending (%s).." % record_pipe.describe(), file=stdout)
    yield pfs.beginFileTransfer(fd_to_send, record_pipe)
    print(u"File sent.. waiting for confirmation", file=stdout)
    ack = yield record_pipe.receive_record()
    record_pipe.close()
    if ack != b"ok\n":
        raise TransferError("Transfer failed (remote says: %r)" % ack)
    print(u"Confirmation received. Transfer complete.", file=stdout)
