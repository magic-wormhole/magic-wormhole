import sys
from twisted.internet.task import react

import msgpack
from attr import frozen

from wormhole import create
from wormhole.transfer_v2 import deferred_transfer
from wormhole.cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory, Protocol


## XXX fixme double define (it's in send.py too)
@frozen
class FileOffer:
    filename: str   # filename (no path segments)
    bytes: int      # total number of bytes in the file

    def to_bytes(self):
        # XXX do we want to give this more structure? e.g. dict?
        return msgpack.dumps([
            "file-offer",
            self.filename,
            self.bytes,
        ])


@frozen
class DirectoryOffer:
    base: str          # unicode path segment of the root directory (i.e. what the user selected)
    size: int          # total number of bytes in _all_ files
    files: list[str]   # a list containing relative paths for each file

    def to_bytes(self):
        return msgpack.packb(["directory-offer", self.base, self.size, self.files])


@frozen
class OfferAccept:
    def to_bytes(self):
        return msgpack.packb(["offer-accept"])


@frozen
class Data:
    data: bytes

    def to_bytes(self):
        return msgpack.packb(["data", self.data])


@frozen
class OfferReject:
    reason: str      # unicode string describing why the offer is rejected

    def to_bytes(self):
        return msgpack.packb(["offer-reject", self.reason])



def decode_message(msg):
    """
    :returns: an instance of one of the message types
    """
    payload = msgpack.loads(msg)
    Class = {
        #0x01: FileOffer,
        "file-offer": FileOffer,
        "directory-offer": DirectoryOffer,
        "data": Data,
    }[payload[0]]
    return Class(*payload[1:])


@react
async def main(reactor):
    code = sys.argv[1]
    w = create(
        u"lothar.com/wormhole/text-or-file-xfer",
        u"ws://localhost:4000/v1",  # RENDEZVOUS_RELAY,
        ##RENDEZVOUS_RELAY,
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
            self.factory.total_data = 0
            self.factory.start = reactor.seconds()

        def dataReceived(self, raw_data):
            # should be an entire record (right??)
            msg = decode_message(raw_data)
            if isinstance(msg, FileOffer):
                print(f"got offer: {msg}")
                self.transport.write(OfferAccept().to_bytes())
            elif isinstance(msg, Data):
                self.factory.total_data += len(msg.data)

        def connectionLost(self, why):
            self.factory.duration = reactor.seconds() - self.factory.start
            print(f"subchannel closed {why}")
            print(f"{self.factory.total_data}")
            through = (self.factory.total_data / (1000.0 * 1000.0)) / self.factory.duration
            print(f"{through} Mb/s")

    print(f"listening: {dilated.listen}")
    port = await dilated.listen.listen(Factory.forProtocol(Receiver))
    print(port)

    await Deferred()
