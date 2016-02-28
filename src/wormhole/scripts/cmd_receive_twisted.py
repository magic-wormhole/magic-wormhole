from __future__ import print_function
import io, json
from zope.interface import implementer
from twisted.internet import interfaces, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from ..twisted.transcribe import Wormhole, WrongPasswordError
from ..twisted.transit import TransitReceiver #, TransitError
from .cmd_receive_blocking import BlockingReceiver, RespondError, APPID
from ..errors import TransferError
from .progress import ProgressPrinter

def receive_twisted(args):
    return TwistedReceiver(args).go()

class TwistedReceiver(BlockingReceiver):

    # TODO: @handle_server_error
    @inlineCallbacks
    def go(self):
        w = Wormhole(APPID, self.args.relay_url)

        rc = yield self._go(w)
        yield w.close()
        returnValue(rc)

    @inlineCallbacks
    def _go(self, w):
        self.handle_code(w)
        verifier = yield w.get_verifier()
        self.show_verifier(verifier)
        them_d = yield self.get_data(w)
        try:
            if "message" in them_d:
                yield self.handle_text(them_d, w)
                returnValue(0)
            if "file" in them_d:
                f = self.handle_file(them_d)
                rp = yield self.establish_transit(w, them_d)
                yield self.transfer_data(rp, f)
                self.write_file(f)
                yield self.close_transit(rp)
            elif "directory" in them_d:
                f = self.handle_directory(them_d)
                rp = yield self.establish_transit(w, them_d)
                yield self.transfer_data(rp, f)
                self.write_directory(f)
                yield self.close_transit(rp)
            else:
                self.msg(u"I don't know what they're offering\n")
                self.msg(u"Offer details:", them_d)
                raise RespondError({"error": "unknown offer type"})
        except RespondError as r:
            data = json.dumps(r.response).encode("utf-8")
            yield w.send_data(data)
            returnValue(1)
        returnValue(0)

    @inlineCallbacks
    def get_data(self, w):
        try:
            them_bytes = yield w.get_data()
        except WrongPasswordError as e:
            raise TransferError(u"ERROR: " + e.explain())
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            raise TransferError(u"ERROR: " + them_d["error"])
        returnValue(them_d)

    @inlineCallbacks
    def handle_text(self, them_d, w):
        # we're receiving a text message
        self.msg(them_d["message"])
        data = json.dumps({"message_ack": "ok"}).encode("utf-8")
        yield w.send_data(data)

    @inlineCallbacks
    def establish_transit(self, w, them_d):
        transit_key = w.derive_key(APPID+u"/transit-key")
        transit_receiver = TransitReceiver(self.args.transit_helper)
        transit_receiver.set_transit_key(transit_key)
        direct_hints = yield transit_receiver.get_direct_hints()
        relay_hints = yield transit_receiver.get_relay_hints()
        data = json.dumps({
            "file_ack": "ok",
            "transit": {
                "direct_connection_hints": direct_hints,
                "relay_connection_hints": relay_hints,
                },
            }).encode("utf-8")
        yield w.send_data(data)

        # now receive the rest of the owl
        tdata = them_d["transit"]
        transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
        transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
        record_pipe = yield transit_receiver.connect()
        returnValue(record_pipe)

    @inlineCallbacks
    def transfer_data(self, record_pipe, f):
        self.msg(u"Receiving (%s).." % record_pipe.describe())

        progress_stdout = self.args.stdout
        if self.args.hide_progress:
            progress_stdout = io.StringIO()
        pfc = ProgressingFileConsumer(f, self.xfersize, progress_stdout)
        record_pipe.connectConsumer(pfc)
        received = yield pfc.when_done
        record_pipe.disconnectConsumer()
        # except TransitError
        if received < self.xfersize:
            self.msg()
            self.msg(u"Connection dropped before full file received")
            self.msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
            returnValue(1) # TODO: exit properly
        assert received == self.xfersize

    @inlineCallbacks
    def close_transit(self, record_pipe):
        yield record_pipe.send_record(b"ok\n")
        yield record_pipe.close()

# based on twisted.protocols.ftp.FileConsumer, but:
#  - finish after 'xfersize' bytes received, instead of connectionLost()
#  - don't close the filehandle when done

@implementer(interfaces.IConsumer)
class ProgressingFileConsumer:
    def __init__(self, f, xfersize, progress_stdout):
        self._f = f
        self._xfersize = xfersize
        self._received = 0
        self._progress = ProgressPrinter(xfersize, progress_stdout)
        self._progress.start()
        self.when_done = defer.Deferred()

    def registerProducer(self, producer, streaming):
        self.producer = producer
        assert streaming

    def write(self, bytes):
        self._f.write(bytes)
        self._received += len(bytes)
        self._progress.update(self._received)
        if self._received >= self._xfersize:
            self._progress.finish()
            d,self.when_done = self.when_done,None
            d.callback(self._received)

    def unregisterProducer(self):
        self.producer = None
        if self.when_done:
            # connection was dropped before all bytes were received
            self.when_done.callback(self._received)
