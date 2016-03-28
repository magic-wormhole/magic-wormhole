from __future__ import print_function
import io, json, binascii, six
from twisted.protocols import basic
from twisted.internet import reactor, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from ..errors import TransferError
from .progress import ProgressPrinter
from ..twisted.transcribe import Wormhole, WrongPasswordError
from ..twisted.transit import TransitSender
from .send_common import (APPID, handle_zero, build_other_command,
                          build_phase1_data)

def send_twisted_sync(args):
    # try to use twisted.internet.task.react(f) here (but it calls sys.exit
    # directly)
    d = defer.Deferred()
    # don't call send_twisted() until after the reactor is running, so
    # that if it raises an exception synchronously, we won't stop the reactor
    # before it starts
    reactor.callLater(0, d.callback, None)
    d.addCallback(lambda _: send_twisted(args))
    rc = []
    def _done(res):
        rc.extend([True, res])
        reactor.stop()
    def _err(f):
        rc.extend([False, f])
        reactor.stop()
    d.addCallbacks(_done, _err)
    reactor.run()
    if rc[0]:
        return rc[1]
    print(str(rc[1]))
    rc[1].raiseException()

@inlineCallbacks
def send_twisted(args):
    assert isinstance(args.relay_url, type(u""))
    handle_zero(args)
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

    w = Wormhole(APPID, args.relay_url, tor_manager, timing=args.timing)

    if fd_to_send:
        transit_sender = TransitSender(args.transit_helper,
                                       no_listen=args.no_listen,
                                       tor_manager=tor_manager,
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
