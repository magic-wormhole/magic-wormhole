from __future__ import print_function
import io, json
from twisted.internet import reactor, defer
from twisted.internet.defer import inlineCallbacks, returnValue
from ..twisted.transcribe import Wormhole, WrongPasswordError
from ..twisted.transit import TransitReceiver
from .cmd_receive_blocking import BlockingReceiver, RespondError, APPID
from ..errors import TransferError
from .progress import ProgressPrinter

def receive_twisted_sync(args):
    # try to use twisted.internet.task.react(f) here (but it calls sys.exit
    # directly)
    d = defer.Deferred()
    # don't call receive_twisted() until after the reactor is running, so
    # that if it raises an exception synchronously, we won't stop the reactor
    # before it starts
    reactor.callLater(0, d.callback, None)
    d.addCallback(lambda _: receive_twisted(args))
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

def receive_twisted(args):
    return TwistedReceiver(args).go()

class TwistedReceiver(BlockingReceiver):

    # TODO: @handle_server_error
    @inlineCallbacks
    def go(self):
        tor_manager = None
        if self.args.tor:
            _start = self.args.timing.add_event("import TorManager")
            from ..twisted.tor_manager import TorManager
            self.args.timing.finish_event(_start)
            tor_manager = TorManager(reactor, timing=self.args.timing)
            # For now, block everything until Tor has started. Soon: launch
            # tor in parallel with everything else, make sure the TorManager
            # can lazy-provide an endpoint, and overlap the startup process
            # with the user handing off the wormhole code
            yield tor_manager.start()

        w = Wormhole(APPID, self.args.relay_url, tor_manager,
                     timing=self.args.timing)

        rc = yield self._go(w, tor_manager)
        yield w.close()
        returnValue(rc)

    @inlineCallbacks
    def _go(self, w, tor_manager):
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
                rp = yield self.establish_transit(w, them_d, tor_manager)
                yield self.transfer_data(rp, f)
                self.write_file(f)
                yield self.close_transit(rp)
            elif "directory" in them_d:
                f = self.handle_directory(them_d)
                rp = yield self.establish_transit(w, them_d, tor_manager)
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
    def establish_transit(self, w, them_d, tor_manager):
        transit_key = w.derive_key(APPID+u"/transit-key")
        transit_receiver = TransitReceiver(self.args.transit_helper,
                                           no_listen=self.args.no_listen,
                                           tor_manager=tor_manager,
                                           timing=self.args.timing)
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

        _start = self.args.timing.add_event("rx file")
        progress_stdout = self.args.stdout
        if self.args.hide_progress:
            progress_stdout = io.StringIO()
        progress = ProgressPrinter(self.xfersize, progress_stdout)

        progress.start()
        received = yield record_pipe.writeToFile(f, self.xfersize,
                                                 progress.update)
        progress.finish()
        self.args.timing.finish_event(_start)

        # except TransitError
        if received < self.xfersize:
            self.msg()
            self.msg(u"Connection dropped before full file received")
            self.msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
            returnValue(1) # TODO: exit properly
        assert received == self.xfersize

    @inlineCallbacks
    def close_transit(self, record_pipe):
        _start = self.args.timing.add_event("ack")
        yield record_pipe.send_record(b"ok\n")
        yield record_pipe.close()
        self.args.timing.finish_event(_start)
