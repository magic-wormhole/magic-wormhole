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

    def start(self, consumer, machine):
        self.consumer = consumer
        self.machine = machine
        self.consumer.registerProducer(self, False)

    def resumeProducing(self):
        """
        IPullProducer API: produce one chunk (only)
        """
        data = self._fp.read(self._chunk_size)
        print("resumeProducing", len(data) if data else -1)
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
    file_data_streamer = FileDataSource(fpath.open("rb"))
    done = Deferred()

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
##        done.callback(None)


    proto._sender = sender = boss.make_offer(send_message, start_streaming, finished)

    print("sending offer")
    sender.send_offer(offer)

    await done
    print("done")
