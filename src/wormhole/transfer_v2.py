from __future__ import absolute_import, print_function, unicode_literals

import struct
from typing import Union, Callable

from attr import define, field
from automat import MethodicalMachine
from zope.interface import implementer

from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, Factory

import msgpack

from . import _interfaces
from ._key import derive_phase_key, encrypt_data
from wormhole.dilatedfile import (
    FileOffer,
    DirectoryOffer,
    OfferAccept,
    OfferReject,
    FileData,
    FileAcknowledge,
    Message,
)


def decode_message(msg):
    """
    :returns: an instance of one of the message types
    """
    kind = msg[0]
    empty_payloads = [0x03]
    Class = {
        0x01: FileOffer,
        0x02: DirectoryOffer,
        0x03: OfferAccept,
        0x04: OfferReject,
        0x05: FileData,
        0x06: FileAcknowledge,
    }[kind]
    if kind in empty_payloads:
        return Class()
    else:
        if kind == 0x05:
            return FileData(msg[1:])
        else:
            payload = msgpack.loads(msg[1:])
            return Class(**payload)


def encode_message(msg):
    """
    :returns: a bytes consisting of the kind byte plus msgpack-encoded
        payload for the given message, which must be one of XXX
    """
    kind = {
        FileOffer: 0x01,
        DirectoryOffer: 0x02,
        OfferAccept: 0x03,
        OfferReject: 0x04,
        FileData: 0x05,
        FileAcknowledge: 0x06,
    }[type(msg)]
    raw_data = msg.marshal()
    kind_byte = struct.pack(">B", kind)
    if raw_data is None:
        return kind_byte
    else:
        if kind == 0x05:
            payload = raw_data
        else:
            payload = msgpack.dumps(msg.marshal())
        return kind_byte + payload


# wormhole: _DeferredWormhole,
async def deferred_transfer(reactor, wormhole, on_error, code=None, offers=None):
    """
    Do transfer protocol over an async wormhole interface
    """

    # XXX FIXME
    if code is None:
        wormhole.allocate_code(2)
        code = await wormhole.get_code()
    else:
        wormhole.set_code(code)
        await wormhole.get_code()
    print("code", code)

    from wormhole.dilatedfile import DilatedFileTransfer

    versions = await wormhole.get_versions()

    try:
        transfer = versions["transfer"]  # XXX transfer-v2
    except KeyError:
        # XXX fall back to "classic" file-trasfer
        raise RuntimeError("Peer doesn't support Dilated tranfer")

    boss = DilatedFileTransfer()
    boss.got_peer_versions(transfer)

    print("waiting to dilate")
    endpoints = wormhole.dilate()

    recv_factory = Factory.forProtocol(Receiver)
    recv_factory.boss = boss

    def accept_always(receiver, offer):
        # an output() will call this predicate, so we can't
        # immediately re-enter with an input()
        reactor.callLater(0, receiver.accept_offer, offer)
    recv_factory.accept_or_reject_p = accept_always

    port = await endpoints.listen.listen(recv_factory)
    print(f"listening: {port}")

    if offers:
        for offer in offers:
            await send_offer(endpoints.connect, wormhole, boss, offer)
    else:
        print("waiting")
        await Deferred()


class Receiver(Protocol):
    _machine = None

    def send_message(self, msg):
        self.transport.write(encode_message(msg))

    def connectionMade(self):
        print("subchannel open")
        self._machine = self.factory.boss.offer_received(self.factory.accept_or_reject_p, self.send_message)
        self._machine.set_trace(lambda *args: print("TRACE", args))

    def dataReceived(self, raw_data):
        # should be an entire record (right??)
        msg = decode_message(raw_data)
        print(f"recv: {msg}")
        print(self._machine)
        self._machine.on_message(msg)

    def connectionLost(self, why):
        print(f"subchannel closed {why}")
        self._machine.subchannel_closed()


from twisted.internet.interfaces import IPullProducer

@implementer(IPullProducer)
class FileDataSource:
    """
    A source of data which is a file, implmented using IPullProducer
    """

    def __init__(self, fp, chunk_size=2**11):
        self._fp = fp
        self._chunk_size = chunk_size
        self._when_done = []

    def when_done(self):
        d = Deferred()
        if self._when_done is None:
            d.callback(None)
        else:
            self._when_done.append(d)
        return d

    def start(self, consumer):
        self.consumer = consumer
        self.consumer.registerProducer(self, False)

    def resumeProducing(self):
        """
        IPullProducer API: produce one chunk (only)
        """
        data = self._fp.read(self._chunk_size)
        print("resumeProducing", len(data) if data else -1)
        if data:
            self.consumer.write(encode_message(FileData(data)))
        else:
            self.stopProducing()

    def stopProducing(self):
        print("stopProducing")
        self.consumer.unregisterProducer()
        self._fp.close()
        notify = self._when_done
        self._when_done = None
        for d in notify:
            d.callback(None)


class Sender(Protocol):
    _connection = None
    _sender = None

    def when_connected(self):
        d = Deferred()
        if self._connection is None:
            self._connection = [d]
        elif self._connection is True:
            d.callback(None)
        else:
            self._connection.append(d)
        return d

    def connectionMade(self):
        print("subchannel open", self)
        notify = self._connection or tuple()
        self._connection = True
        for d in notify:
            d.callback(None)

    def dataReceived(self, raw_data):
        # should be an entire record (right??)
        print(f"recv: {raw_data}")
        msg = decode_message(raw_data)
        print(f"parsed: {msg}")
        out_msg = self._sender.on_message(msg)
        if out_msg:
            print(f"have outgoing: {out_msg}")
            self.transport.write(encode_message(out_msg))

    def connectionLost(self, why):
        print(f"subchannel closed {why}")
        self._sender.subchannel_closed()


async def send_offer(connect_ep, wormhole, boss, fpath):
    proto = await connect_ep.connect(Factory.forProtocol(Sender))
    print("proto", proto)
    await proto.when_connected()

    # XXX need a whole different state-machine for directories i think..
    assert fpath.is_file(), "file must exist and be a file"
    offer = FileOffer(fpath.name, fpath.stat().st_mtime, fpath.stat().st_size)

    # XXX probably want to give the machine some stuff? like, the
    # offer and "a way to send data"? and "a way to close"?
    # hook up "connection lost" -> sender.subchannel_closed

    file_data_streamer = FileDataSource(fpath.open("rb"))
    sender = boss.make_offer()
    sender._message_encoder = encode_message
    d = Deferred()
    sender._on_ready = lambda: d.callback(None)
    proto._sender = sender
    print("sending offer")
    # XXX hmm .. should the 'state machine' do sending etc, or do we
    # just tell it "oh, we sent the offer ...? or we give it
    # callbacks? (but then those callbacks might be 'async def' or
    # not)
    outmsg = sender.send_offer(offer)
    proto.transport.write(outmsg)
    await d
    # XXX this isn't right .. we need to pass the data through the state machine
    # ...and it has a send_message() so then it encodes into the right "msg" and sends
    # ...and it has to _ask_ us to start streaming
    # ...so then we tell the transport to start sucking data, basically?

    def start_streaming():
        print("ready to send data...")
        file_data_streamer.start(proto.transport)
        print("started")
        d = Deferred.fromCoroutine(file_data_streamer.when_done())
        d.addCallbacks(
            lambda _: sender.data_finished(),
            lambda _: sender.error(),
        )

    # XXX if we don't do this, other side doesn't get a close
    # .. unclean shutdown should try to close subchannels?
    proto.transport.loseConnection()




@define
class TransferV2(object):
    """
    Speaks both ends of the Transfer v2 application protocol
    """
    m = MethodicalMachine()

    # XXX might make more sense to have this on the outside, only
    # .. i.e. "something" handles all the async stuff? ... so here we
    # just get "a Callable to send stuff"
    # endpoints: EndpointRecord

    send_control_message: Callable[[Union[FileOffer, DirectoryOffer, OfferReject, OfferAccept]], None]
    send_file_in_offer: Callable[[FileOffer, Callable[[], None]], None]
    receive_file_in_offer: Callable[[FileOffer, Callable[[], None]], None]

    _queued_offers = field(factory=list)
    _offers = field(factory=dict)
    _peer_offers = field(factory=dict)
    _when_done = field(factory=list)

    # XXX OneShotObserver
    def when_done(self):
        d = Deferred()
        if self._when_done is None:
            d.callback(None)
        else:
            self._when_done.append(d)
        return d

    @m.state(initial=True)
    def await_dilation(self):
        """
        The Dilated connection has not yet succeeded
        """

    @m.state()
    def connected(self):
        """
        We are connected to the peer via Dilation.
        """

    @m.state()
    def closing(self):
        """
        Shutting down and waiting for confirmation
        """

    @m.state()
    def done(self):
        """
        Completed and disconnected.
        """

    @m.input()
    def make_offer(self, offer):
        """
        Present an offer to the peer
        """
        # XXX offer should be some type so we don't have to check it
        # for semantics etc

    @m.input()
    def dilated(self):
        """
        The wormhole Dilation has succeeded
        """

    @m.input()
    def got_accept(self, accept):
        """
        Our peer has accepted a transfer
        """

    @m.input()
    def got_reject(self, reject):
        """
        Our peer has rejected a transfer
        """

    @m.input()
    def got_offer(self, offer):
        """
        Our peer has sent an offer
        """

    @m.input()
    def accept_offer(self, offer_id):
        """
        Accept an offer our peer has previously sent
        """

    @m.input()
    def stop(self):
        """
        We wish to end the transfer session
        """

    @m.input()
    def mailbox_closed(self):
        """
        Our connection has been closed
        """

    @m.output()
    def _queue_offer(self, offer):
        self._queued_offers.append(offer)

    @m.output()
    def _send_queued_offers(self):
        to_send = self._offers
        self._offers = None  # can't go back to await_dilation
        for offer in to_send:
            self.send_control_message(offer)
            self._offers[offer.id] = offer

    @m.output()
    def _send_offer(self, offer):
        self.send_control_message(offer)
        self._offers[offer.id] = offer

    @m.output()
    def _send_file(self, accept):
        # XXX if not found, protocol-error
        offer = self._offers[accept.id]

        def on_sent():
            del self._offers[accept.id]
        self.send_file_in_offer(offer, on_sent)

    @m.output()
    def _receive_file(self, offer_id):
        # XXX if not found, protocol-error
        peer_offer = self._peer_offers[offer_id]

        def on_received():
            del self._peer_offers[offer_id]
        self.receive_file_in_offer(peer_offer, on_received)
        # pattern like ^ means who cares if this is async or not
        # .. on_received called when the transfer is done.

    @m.output()
    def _remember_offer(self, offer):
        self._peer_offers[offer.id] = offer

    @m.output()
    def _remove_offer(self, reject):
        # XXX if not found, protocol-error
        del self._offers[reject.id]

    @m.output()
    def _close_mailbox(self):
        pass

    @m.output()
    def _notify_done(self):
        done = self._when_done
        self._when_done = None
        for d in done:
            d.callback(None)

    await_dilation.upon(
        make_offer,
        enter=await_dilation,
        outputs=[_queue_offer],
    )
    await_dilation.upon(
        dilated,
        enter=connected,
        outputs=[_send_queued_offers],
    )

    connected.upon(
        make_offer,
        enter=connected,
        outputs=[_send_offer],
    )
    connected.upon(
        got_accept,
        enter=connected,
        outputs=[_send_file]
    )
    connected.upon(
        got_reject,
        enter=connected,
        outputs=[_remove_offer]
    )
    connected.upon(
        got_offer,
        enter=connected,
        outputs=[_remember_offer]
    )
    connected.upon(
        stop,
        enter=closing,
        outputs=[_close_mailbox],
    )
    connected.upon(
        accept_offer,
        enter=connected,
        outputs=[_receive_file],
    )

    closing.upon(
        mailbox_closed,
        enter=done,
        outputs=[_notify_done],
    )
