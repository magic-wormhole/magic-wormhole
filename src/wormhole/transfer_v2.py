from __future__ import absolute_import, print_function, unicode_literals

import struct
from typing import Union, Callable

from attr import define, field
from automat import MethodicalMachine
from zope.interface import implementer

from twisted.internet.defer import Deferred, maybeDeferred, DeferredList
from twisted.internet.protocol import Protocol, Factory
from twisted.python.filepath import FilePath

import msgpack

from . import _interfaces
from ._key import derive_phase_key, encrypt_data
from .observer import OneShotObserver
from .eventual import EventualQueue
from wormhole.dilatedfile import (
    FileOffer,
    DirectoryOffer,
    OfferAccept,
    OfferReject,
    FileData,
    FileAcknowledge,
    Message,
    DilatedFileTransfer,
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


def decode_control_message(raw_data):
    """
    Decodes an incoming control-channel message, raising exception on
    error.
    """
    msg = msgpack.loads(raw_data)
    try:
        kind = msg["kind"]
    except KeyError:
        raise Exception("Control messages must include 'kind' field")
    if kind == "text":
        return Message(msg["message"])
    raise Exception("Unknown control message '{}'".format(kind))


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



#XXX fixme notes
#
# if we want this to do ALL kinds of offers (probably), then:
# - need "offer async-iterator" or similar (e.g. chat application doesn't even know how _many_ offers there will be)
# - receiver should have an "accept_offer_p" that returns ... something ("open file" for file offers, "some kind of directory API instance" for directory offers, callback for text-message offers?)
#     - so it needs some 'context' object
#     - ...and separate sub-state-machines for each kind of offer
#     - ...and more generic "make_offer()" function?

# wormhole: _DeferredWormhole,
async def deferred_transfer(reactor, wormhole, on_error, on_message=None, transit=None, code=None, offers=None, receive_directory=None, next_message=None):
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

    if receive_directory is None:
        receive_directory = FilePath(".")

    versions = await wormhole.get_versions()

    try:
        transfer = versions["transfer"]  # XXX transfer-v2
    except KeyError:
        # XXX fall back to "classic" file-trasfer
        raise RuntimeError("Peer doesn't support Dilated tranfer")

    boss = DilatedFileTransfer()
    boss.got_peer_versions(transfer)

    endpoints = wormhole.dilate(transit)

    recv_factory = Factory.forProtocol(Receiver)
    recv_factory.boss = boss

    def accept_always(receiver, offer):
        # an @output() will call this predicate, so we can't
        # immediately re-enter with an @input()
        the_file = receive_directory.child(offer.filename).open("wb")
        reactor.callLater(0, receiver.accept_offer, offer, the_file)
    recv_factory.accept_or_reject_p = accept_always

    port = await endpoints.listen.listen(recv_factory)

    class Control(Protocol):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self._closed = OneShotObserver(EventualQueue(reactor))

        def when_closed(self):
            return self._closed.when_fired()

        def dataReceived(self, data):
            if on_message is not None:
                msg = decode_control_message(data)
                on_message(msg)

        def connectionLost(self, reason):
            self._closed.fire(None)

    control_proto = await endpoints.control.connect(Factory.forProtocol(Control))

    if offers:
        # could send in parallel ...
        for offer in offers:
            await send_file_offer(endpoints.connect, wormhole, boss, offer)
        # close control channel (we're done)
        control_proto.transport.loseConnection()

    # if we have next_message, wait for one of those .. or just for
    # control channel to close
    control_closed_d = control_proto.when_closed()
    while True:
        got_message_d = Deferred() if next_message is None else maybeDeferred(next_message)
        result, index = await DeferredList(
            [control_closed_d, got_message_d],
            fireOnOneCallback=True,
            fireOnOneErrback=True,
        )
        if index == 0:  # "control_closed_d" fired
            break
        offer_text = await got_message_d
        msg = Message(offer_text)
        encoded = msgpack.dumps(msg.marshal())
        control_proto.transport.write(encoded)
        print(">>> sent {} bytes".format(len(encoded)))

    await wormhole.close()


class Receiver(Protocol):
    _machine = None

    def send_message(self, msg):
        self.transport.write(encode_message(msg))

    def connectionMade(self):
        self._machine = self.factory.boss.offer_received(self.factory.accept_or_reject_p, self.send_message)
        self._machine.set_trace(lambda *args: print("TRACE", args))

    def dataReceived(self, raw_data):
        # should be an entire record (right??)
        msg = decode_message(raw_data)
        ##print(f"recv: {type(msg)}")
        self._machine.on_message(msg)

    def connectionLost(self, why):
        ##print(f"subchannel closed {why}")
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

    def start(self, consumer, machine):
        self.consumer = consumer
        self.machine = machine
        self.consumer.registerProducer(self, False)

    def resumeProducing(self):
        """
        IPullProducer API: produce one chunk (only)
        """
        data = self._fp.read(self._chunk_size)
        ##print("resumeProducing", len(data) if data else -1)
        if data:
            # we want all data to go through the state-machine; it
            # will call send_message which will write to our consumer
            # (the protocol transport)
            self.machine.send_data(data)
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
    _disconnection = None
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

    def when_closed(self):
        d = Deferred()
        if self._disconnection is None:
            self._disconnection = [d]
        elif self._disconnection is True:
            d.callback(None)
        else:
            self._disconnection.append(d)
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
        notify = self._disconnection
        self._disconnection = True
        if notify:
            for d in notify:
                d.callback(None)


async def send_file_offer(connect_ep, wormhole, boss, fpath):
    proto = await connect_ep.connect(Factory.forProtocol(Sender))
    print("proto", proto)
    await proto.when_connected()

    # XXX need a whole different state-machine for directories i think..
    assert fpath.is_file(), "file must exist and be a file"
    offer = FileOffer(fpath.name, fpath.stat().st_mtime, fpath.stat().st_size)
    file_data_streamer = FileDataSource(fpath.open("rb"))

    def send_message(msg):
        proto.transport.write(encode_message(msg))

    def start_streaming():
        print("ready to send data...")
        file_data_streamer.start(proto.transport, sender)
        print("started")
        d = file_data_streamer.when_done()
        d.addCallbacks(
            lambda _: sender.data_finished(),
            lambda _: sender.error(),
        )

    def finished():
        print("finished")
        proto.transport.loseConnection()


    proto._sender = sender = boss.make_offer(send_message, start_streaming, finished)

    print("sending offer")
    sender.send_offer(offer)

    await proto.when_closed()
    print("done")
