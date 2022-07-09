import sys
from twisted.internet.task import react

import msgpack
from attr import define

from wormhole import create
from wormhole.transfer_v2 import deferred_transfer
from wormhole.cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory, Protocol


## XXX fixme double define
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


def decode_message(msg):
    """
    :returns: an instance of one of the message types
    """
    kind = msg[0]
    payload = msgpack.loads(msg[1:])
    Class = {
        0x01: FileOffer,
    }[kind]
    return Class(**payload)


@react
async def main(reactor):
    code = sys.argv[1]
    w = create(
        u"lothar.com/wormhole/text-or-file-xfer",
        ##u"ws://localhost:4000/v1",  # RENDEZVOUS_RELAY,
        RENDEZVOUS_RELAY,
        reactor,
        _enable_dilate=True,
        versions={
            "transfer": {
                "mode": "receive",
                "features": {},
            }
        }
    )
    w.set_code(code)
    code = await w.get_code()
    print(f"code: {code}")
    versions = await w.get_versions()
    print("versions: {}".format(versions))

    dilated = w.dilate()

    class Receiver(Protocol):

        def connectionMade(self):
            print("subchannel open")

        def dataReceived(self, raw_data):
            # should be an entire record (right??)
            msg = decode_message(raw_data)
            print(f"recv: {msg}")

        def connectionLost(self, why):
            print(f"subchannel closed {why}")

    print(f"listening: {dilated.listen}")
    port = await dilated.listen.listen(Factory.forProtocol(Receiver))
    print(port)

    await Deferred()
