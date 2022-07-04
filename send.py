from twisted.internet.task import react

import struct

import msgpack
from attr import define

from wormhole import create
from wormhole.transfer_v2 import deferred_transfer
from wormhole.cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory, Protocol


@define
class FileOffer:
    filename: str   # filename (no path segments)
    timestamp: int  # Unix timestamp (seconds since the epoch in GMT)
    bytes: int      # total number of bytes in the file

    def marshal(self):
        return {
            "filename": self.filename,
            "timestamp": self.timestamp,
            "bytes": self.bytes,
        }


def encode_message(msg):
    """
    :returns: a bytes consisting of the kind byte plus msgpack-encoded
        payload for the given message, which must be one of XXX
    """
    payload = msgpack.dumps(msg.marshal())
    kind = {
        FileOffer: 0x01,
    }[type(msg)]
    return struct.pack(">B", kind) + payload


if False:
    offer = FileOffer(
        filename="foo",
        timestamp=0,
        bytes=42,
    )
    print(encode_message(offer))
    import sys ; sys.exit(0)


@react
async def main(reactor):
    w = create(
        u"lothar.com/wormhole/text-or-file-xfer",
        u"ws://localhost:4000/v1",  # RENDEZVOUS_RELAY,
        reactor,
        _enable_dilate=True,
        versions={
            "transfer": {
                "mode": "receive",
                "features": ["basic"],
                "permission": "ask",
            }
        }
    )
    w.allocate_code(2)
    code = await w.get_code()
    print(f"code: {code}")
    versions = await w.get_versions()
    print("versions: {}".format(versions))
    dilated = w.dilate()
    print("dilated: {}".format(dilated))

    # open a subchannel, i.e. pretend to do offer

    class Sender(Protocol):

        def connectionMade(self):
            print("subchannel open")
            # XXX send offer
            offer = FileOffer(
                filename="foo",
                timestamp=0,
                bytes=42,
            )
            self.transport.write(
                encode_message(offer)
            )

        def dataReceived(self, raw_data):
            # should be an entire record (right??)
            print(f"recv: {raw_data}")
            data = msgpack.loads(raw_data)
            print(f"parsed: {data}")

        def connectionLost(self, why):
            print(f"subchannel closed {why}")

    print(f"connecting: {dilated.connect}")
    proto = await dilated.connect.connect(Factory.forProtocol(Sender))
    print(proto)

    await Deferred()
