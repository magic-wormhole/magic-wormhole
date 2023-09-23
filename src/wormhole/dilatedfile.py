
# prototype state-machine "diagrams" / skeleton code for Dilated File
# Transfer

from hashlib import blake2b
from attr import define, field
from automat import MethodicalMachine
from twisted.internet.defer import Deferred  # danger..are we sans-io?


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


@define
class DirectoryOffer:
    base: str          # unicode pathname of the root directory (i.e. what the user selected)
    size: int         # total number of bytes in _all_ files
    files: list[str]   # a list containing relative paths for each file


@define
class OfferAccept:
    def marshal(self):
        return None  # no body


@define
class OfferReject:
    reason: str      # unicode string describing why the offer is rejected

    def marshal(self):
        return {
            "reason": self.reason,
        }


@define
class FileData:
    data: bytes  # raw binary data

    def marshal(self):
        return self.data


@define
class FileAcknowledge:
    bytes: int   # total number of bytes in the file
    hash: bytes  # 32-byte Blake2b hash of the file

    def marshal(self):
        return {
            "bytes": self.bytes,
            "hash": self.hash,  # encode it?
        }


@define
class Message:
    message: str     # unicode string

    def marshal(self):
        return {
            "message": self.message,
            "kind": "text",
        }


def _last_one(things):
    """
    Used as a 'collector' for some Automat state transitions. Usually
    the only interesting object comes from the final output (and using
    this collector indicates that).

    :returns: the last thing in the iterable
    """
    return list(things)[-1] if things else None


## can we make a generic machine here?
## or do we actually _want_ different machines for file vs directory vs text?
## (probably the latter? they're pretty different processes...)


class DilatedFileSender:
    """
    Manages the sending of a single file
    """
    m = MethodicalMachine()

    def __init__(self, send_message, start_streaming, finished):
        self._send_message = send_message
        self._start_streaming = start_streaming
        self._finished = finished
        self._hasher = blake2b(digest_size=32)
        self._bytes = 0

    def on_message(self, msg):
        """
        A message has been received; act on it.
        """
        if isinstance(msg, OfferAccept):
            return self.offer_accepted()
        elif isinstance(msg, OfferReject):
            return self.offer_rejected()
        elif isinstance(msg, FileAcknowledge):
            return self.acknowledge_received(msg)
        else:
            raise Exception(f"Unknown message: {msg}")

    @m.state(initial=True)
    def start(self):
        """
        Initial state
        """

    @m.state()
    def permission(self):
        """
        Waiting for a yes/no about an offer
        """

    @m.state()
    def sending(self):
        """
        Streaming file data
        """

    @m.state()
    def await_acknowledge(self):
        """
        Wait for the acknowledge from the other side
        """

    @m.state()
    def closing(self):
        """
        Waiting for confirmation the subchannel is closed
        """

    @m.state(terminal=True)
    def closed(self):
        """
        Completed operation.
        """

    @m.input()
    def send_offer(self, offer):
        """
        An offer is ready to be made

        :returns: a Deferred that fires when data may be sent
        """

    @m.input()
    def offer_accepted(self):
        """
        The peer has accepted our offer
        """

    @m.input()
    def offer_rejected(self):
        """
        The peer has rejected our offer
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.input()
    def send_data(self, data):
        """
        Try to send some more data
        """

    @m.input()
    def data_finished(self):
        """
        There is no more data to send
        """

    @m.input()
    def acknowledge_received(self, acknowledge):
        """
        The other peer's FileAcknowledge message is received
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has been closed
        """

    @m.output()
    def _send_offer(self, offer):
        self._send_message(offer)

    @m.output()
    def _push_data(self):
        self._start_streaming()

    @m.output()
    def _send_data(self, data):
        print("SEND", len(data))
        self._bytes += len(data)
        self._hasher.update(data)
        self._send_message(FileData(data))

    @m.output()
    def _close_input_file(self):
        pass

    @m.output()
    def _send_acknowledge(self):
        msg = FileAcknowledge(self._bytes, self._hasher.digest())
        self._send_message(msg)

    @m.output()
    def _check_acknowledge(self, acknowledge):
        if acknowledge.bytes != self._bytes:
            raise Exception(f"expected {self._bytes} bytes but got {acknowledge_msg.bytes}")
        if acknowledge.hash != self._hasher.digest():
            raise Exception("hash mismatch")

    @m.output()
    def _close_subchannel(self):
        self._finished()

    @m.output()
    def _notify_accepted(self):
        """
        Peer has accepted and streamed entire file
        """

    @m.output()
    def _notify_rejected(self):
        """
        Peer has rejected the file
        """

    @m.output()
    def _notify_done(self):
        """
        Completed our journey
        """
        pass  # _finished() should actually be "close_subchannel", approx?

    start.upon(
        send_offer,
        enter=permission,
        outputs=[_send_offer],
        collector=_last_one,
    )

    permission.upon(
        offer_accepted,
        enter=sending,
        outputs=[_push_data],
        collector=_last_one,
    )
    permission.upon(
        offer_rejected,
        enter=closing,
        outputs=[_close_input_file, _close_subchannel, _notify_rejected],
        collector=_last_one,
    )

    sending.upon(
        send_data,
        enter=sending,
        outputs=[_send_data],
        collector=_last_one,
    )
    sending.upon(
        data_finished,
        enter=await_acknowledge,
        outputs=[_close_input_file, _send_acknowledge],
        collector=_last_one,
    )

    await_acknowledge.upon(
        acknowledge_received,
        enter=closing,
        outputs=[_check_acknowledge, _close_subchannel, _notify_accepted],
        collector=_last_one,
    )

    closing.upon(
        subchannel_closed,
        enter=closed,
        outputs=[_notify_done],
        collector=_last_one,
    )


class DilatedFileReceiver:
    """
    Manages the receiving of a single FileOffer
    """
    m = MethodicalMachine()

    def __init__(self, accept_or_reject_p, send_message):
        self._accept_or_reject = accept_or_reject_p
        self._send_message = send_message
        self._hasher = blake2b(digest_size=32)  # compute the hash of received bytes
        self._bytes = 0  # how many bytes we've received

    set_trace = getattr(m, "_setTrace", lambda self, f: None)

    def on_message(self, msg):
        """
        A message has been received; act on it.
        """
        if isinstance(msg, FileOffer):
            return self.offer_received(msg)
        elif isinstance(msg, FileData):
            return self.data_received(msg.data)
        elif isinstance(msg, FileAcknowledge):
            return self.acknowledge_received(msg)
        else:
            raise Exception(f"Unknown message: {msg}")

    @m.state(initial=True)
    def await_offer(self):
        """
        Waiting for an Offer
        """

    @m.state()
    def permission(self):
        """
        Waiting for a yes/no about an Offer
        """

    @m.state()
    def receive_data(self):
        """
        Accept incoming file-data
        """

    @m.state()
    def closing(self):
        """
        Waiting for confirmation the subchannel is closed
        """

    @m.state(terminal=True)
    def closed(self):
        """
        Completed operation.
        """

    @m.input()
    def offer_received(self, offer):
        """
        The peer has sent the FileOffer
        """

    @m.input()
    def acknowledge_received(self, acknowledge_msg):
        """
        The peer has send a FileAcknowledge
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.input()
    def accept_offer(self, offer, file_like):
        """
        A decision to accept the offer
        """

    @m.input()
    def reject_offer(self, offer):
        """
        A decision to reject the offer
        """

    @m.input()
    def data_received(self, data):
        """
        The peer has send data to us.
        """

    @m.input()
    def received_acknowledge(self, acknowledge_msg):
        """
        All expected data is received
        """

    @m.input()
    def unexpected_error(self, e):
        """
        Something unexpected happened
        """

    @m.output()
    def _ask_about_offer(self, offer):
        """
        Use a callback to ask if this offer should be accepted; the
        callback should inject the correct event when it is ready
        (which may be immediately or after some time if e.g. waiting
        for a GUI).
        """
        self._accept_or_reject(self, offer)

    @m.output()
    def _close_output_file(self):
        self._output.close()

    @m.output()
    def _send_accept(self, offer, file_like):
        self._output = file_like
        msg = OfferAccept()
        self._send_message(msg)

    @m.output()
    def _send_reject(self, offer):
        msg = OfferReject("Offer rejected")  # XXX user-defined message?
        self._send_message(msg)

    @m.output()
    def _send_acknowledge(self):
        msg = FileAcknowledge(self._bytes, self._hasher.digest())
        self._send_message(msg)

    @m.output()
    def _check_acknowledge(self, acknowledge_msg):
        if acknowledge_msg.bytes != self._bytes:
            raise Exception(f"expected {self._bytes} bytes but got {acknowledge_msg.bytes}")
        if acknowledge_msg.hash != self._hasher.digest():
            raise Exception("hash mismatch")

    @m.output()
    def _close_subchannel(self):
        pass

    @m.output()
    def _write_data_to_file(self, data):
        """
        Writing incoming data to our file
        """
        self._bytes += len(data)
        self._hasher.update(data)
        self._output.write(data)

    await_offer.upon(
        offer_received,
        enter=permission,
        outputs=[_ask_about_offer],
        collector=_last_one,
    )

    permission.upon(
        accept_offer,
        enter=receive_data,
        outputs=[_send_accept],
        collector=_last_one,
    )
    permission.upon(
        reject_offer,
        enter=closing,
        outputs=[_send_reject, _close_subchannel],
        collector=_last_one,
    )

    receive_data.upon(
        data_received,
        enter=receive_data,
        outputs=[_write_data_to_file],
        collector=_last_one,
    )
    receive_data.upon(
        acknowledge_received,
        enter=closing,
        outputs=[_close_output_file, _send_acknowledge, _check_acknowledge],
        collector=_last_one,
    )

    # receive_data.upon(
    #     subchannel_closed,
    #     enter=error,
    #     outputs=[_cleanup_file],
    #     collector=_last_one,
    # )

    closing.upon(
        subchannel_closed,
        enter=closed,
        outputs=[],
        collector=_last_one,
    )


class DilatedFileTransfer(object):
    """
    Manages transfers for the Dilated File Transfer protocol
    """
    m = MethodicalMachine()

    def got_peer_versions(self, versions):
        mode = versions["mode"]
        if mode == "send":
            return self.peer_send()
        elif mode == "receive":
            return self.peer_receive()
        elif mode == "connect":
            return self.peer_connect()
        else:
            raise Exception(f'protocol error: invalid mode "{mode}"')

    @m.state(initial=True)
    def dilated(self):
        """
        Dilated connection is open
        """

    @m.state()
    def receiving(self):
        """
        Peer will only send
        """

    @m.state()
    def sending(self):
        """
        Peer will only receive
        """

    @m.state()
    def connect(self):
        """
        Peer will send and/or receive
        """

    @m.state()
    def closing(self):
        """
        Shutting down.
        """

    @m.state()
    def closed(self):
        """
        Completed operation.
        """

    @m.input()
    def peer_send(self):
        """
        Peer is in mode 'send'
        """

    @m.input()
    def peer_receive(self):
        """
        Peer is in mode 'receive'
        """

    @m.input()
    def peer_connect(self):
        """
        Peer is in mode 'connect'
        """

    @m.input()
    def got_file_offer(self, offer):
        """
        We have received a FileOffer from our peer
        """
        # XXX DirectoryOffer conceptually similar, but little harder

    # _can_ we make a generic 'make_offer' method, or is that foolish
    # and we should have one per offer-type anyway?
    @m.input()
    def make_offer(self, send_message, start_streaming, finished):
        """
        Make an offer machine so we can give the other peer a file /
        directory.
        """

    @m.input()
    def offer_received(self, accept_or_reject_p, send_message):
        """
        Make a receive machine so we can accept a file / directory.
        """

    @m.input()
    def dilation_closed(self):
        """
        The dilated connection has been closed down
        """

    @m.output()
    def _create_receiver(self, accept_or_reject_p, send_message):
        """
        Make a DilatedFileReceiver
        """
        return DilatedFileReceiver(accept_or_reject_p, send_message)

    @m.output()
    def _create_sender(self, send_message, start_streaming, finished):
        """
        Make a DilatedFileSender
        """
        return DilatedFileSender(send_message, start_streaming, finished)

    @m.output()
    def _protocol_error(self):
        """
        The peer hasn't spoken correctly.
        """

    @m.output()
    def _close_dilation(self):
        """
        Shut down the dilated conection.
        """

    @m.output()
    def _cleanup_outgoing(self):
        """
        Abandon any in-progress sends
        """

    @m.output()
    def _cleanup_incoming(self):
        """
        Abandon any in-progress receives
        """

    # XXX we want some error-handling here, like if both peers are
    # mode=send or both are mode=receive
    dilated.upon(
        peer_receive,
        enter=sending,
        outputs=[],
        collector=_last_one,
    )

    dilated.upon(
        peer_send,
        enter=receiving,
        outputs=[],
        collector=_last_one,
    )

    dilated.upon(
        peer_connect,
        enter=connect,
        outputs=[],
        collector=_last_one,
    )

    sending.upon(
        make_offer,
        enter=sending,
        outputs=[_create_sender],
        collector=_last_one,
    )
    sending.upon(
        offer_received,
        enter=closing,
        outputs=[_protocol_error, _close_dilation],
        collector=_last_one,
    )
    sending.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_outgoing],
        collector=_last_one,
    )

    receiving.upon(
        offer_received,
        enter=receiving,
        outputs=[_create_receiver],
        collector=_last_one,
    )
    receiving.upon(
        make_offer,
        enter=closing,
        outputs=[_protocol_error, _close_dilation],
        collector=_last_one,
    )
    receiving.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_incoming],
        collector=_last_one,
    )

    connect.upon(
        offer_received,
        enter=connect,
        outputs=[_create_receiver],
        collector=_last_one,
    )
    connect.upon(
        make_offer,
        enter=connect,
        outputs=[_create_sender],
        collector=_last_one,
    )
    connect.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_outgoing, _cleanup_incoming],
        collector=_last_one,
    )

    closing.upon(
        dilation_closed,
        enter=closed,
        outputs=[],
        collector=_last_one,
    )
