from twisted.internet.task import react

import tracemalloc
tracemalloc.start()


import struct

import msgpack
from attr import frozen

from wormhole import create
from wormhole.transfer_v2 import deferred_transfer
from wormhole.cli.public_relay import RENDEZVOUS_RELAY, TRANSIT_RELAY
from wormhole.observer import OneShotObserver
from wormhole.eventual import EventualQueue
from twisted.internet import task
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Factory, Protocol


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
class OfferAccept:
    def to_bytes(self):
        return msgpack.packb(["offer-accept"])


@frozen
class OfferReject:
    reason: str      # unicode string describing why the offer is rejected

    def to_bytes(self):
        return msgpack.packb(["offer-reject", self.reason])


@frozen
class DirectoryOffer:
    base: str          # unicode path segment of the root directory (i.e. what the user selected)
    size: int          # total number of bytes in _all_ files
    files: list[str]   # a list containing relative paths for each file

    def to_bytes(self):
        return msgpack.packb(["directory-offer", self.base, self.size, self.files])


if False:
    offer = FileOffer("foo", 42)
    print(encode_message(offer))
    import sys ; sys.exit(0)

class FileProducer:

    def __init__(self, reactor, open_filelike, bytes_to_read, msg_size=2**16 - 10):
        self._file = open_filelike
        self._bytes_to_read = bytes_to_read
        self._bytes_read = 0
        self._cooperate = task.cooperate
        self._msgsize = msg_size
        assert msg_size <= 65526, "data cannot be larger than 65526 bytes"
        self._done = OneShotObserver(EventualQueue(reactor))

    def when_done(self):
        return self._done.when_fired()

    def stopProducing(self):
        """
        Permanently stop writing bytes from the file to the consumer by
        stopping the underlying L{CooperativeTask}.
        """
        self._file.close()
        try:
            self._task.stop()
        except task.TaskFinished:
            pass
        self._done.fire(None)

    def startProducing(self, consumer):
        """
        Start a cooperative task which will read bytes from the input file and
        write them to `consumer`.

        If the returned `Deferred` (which fires after all bytes have
        been written is cancelled then stop reading and writing bytes.
        """
        consumer.registerProducer(self, True)
        self._task = self._cooperate(self._writeloop(consumer))
        d = self._task.whenDone()

        def maybeStopped(reason):
            if reason.check(defer.CancelledError):
                self.stopProducing()
            elif reason.check(task.TaskStopped):
                pass
            else:
                return reason
            # we do not fire the Deferred if stopProducing is called.
            print("weirdo") x
            return Deferred()

        d.addCallbacks(lambda ignored: None, maybeStopped)
        return d

    def _writeloop(self, consumer):
        """
        Return an iterator which reads one chunk of bytes from the input file
        and writes them to the consumer for each time it is iterated.
        """
        while True:
            to_read = min(self._bytes_to_read - self._bytes_read, self._msgsize)
            b = self._file.read(to_read)
            if not bytes:
                self._file.close()
                break

            self._bytes_read += len(b)
            # encode into a "data" message
            data = msgpack.dumps(["data", b])
            consumer.write(data)
            del b
            del data
            if self._bytes_read >= self._bytes_to_read:
                print("done")
                self._file.close()
                break
            yield None

    def pauseProducing(self):
        """
        Temporarily suspend copying bytes from the input file to the consumer
        by pausing the L{CooperativeTask} which drives that activity.
        """
        ##print("pause producing")
        self._task.pause()

    def resumeProducing(self):
        """
        Undo the effects of a previous C{pauseProducing} and resume copying
        bytes to the consumer by resuming the L{CooperativeTask} which drives
        the write activity.
        """
        ##print("resume producing")
        self._task.resume()


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
                "features": ["core0"],
            }
        }
    )
    import sys
    if len(sys.argv) > 1:
        w.set_code(sys.argv[1])
    else:
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
            self.transport.write(self.factory.offer.to_bytes())

        def dataReceived(self, raw_data):
            # should be an entire record (right??)
            data = msgpack.loads(raw_data)
            msg = {
                "offer-accept": OfferAccept,
                "offer-reject": OfferReject,
            }[data[0]](*data[1:])
            print(f"parsed: {msg}")
            if isinstance(msg, OfferAccept):
                self.factory.d = self.factory.data_producer.startProducing(self.transport)
                def done(x):
                    print("DONE", x)
                    self.transport.loseConnection()
                    return x
                self.factory.d.addCallback(done)

        def connectionLost(self, why):
            print(f"subchannel closed {why}")

    print(f"connecting: {dilated.connect}")
    factory = Factory.forProtocol(Sender)

    snapshots = []

    factory.offer = FileOffer("foo", 42*1000*1000)
    factory.data_producer = FileProducer(
        reactor,
        open("/dev/urandom", "rb"),
        factory.offer.bytes,
        2**14, ##2**16 - 10,
    )

    def snapshot():
        r = factory.data_producer._bytes_read
        pct = int((float(r) / float(factory.offer.bytes)) * 100.0)
        print(f"{int(reactor.seconds())}s: {r} {pct}")
        ##print("snapshot", reactor.seconds())
        snapshots.append(tracemalloc.take_snapshot())
        if False and len(snapshots) > 2:
            diff = snapshots[-1].compare_to(snapshots[-2], "lineno")
            for stat in diff[:10]:
                print(stat)

    task.LoopingCall(snapshot).start(5)

    # XXX this doesn't exit properly yet (stopProducing never called?)
    proto = await dilated.connect.connect(factory)
    await factory.data_producer.when_done()
    await w.close()
