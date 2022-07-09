
# prototype state-machine "diagrams" / skeleton code for Dilated File
# Transfer

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
    kind: str = "text"


def _last_one(things):
    """
    Used as a 'collector' for some Automat state transitions. Usually
    the only interesting object comes from the final output (and using
    this collector indicates that).

    :returns: the last thing in the iterable
    """
    return list(things)[-1] if things else None


class DilatedFileSender:
    """
    Manages the sending of a single file
    """
    m = MethodicalMachine()

    def __init__(self, send_message):
        self._send_message = send_message

    def on_message(self, msg):
        """
        A message has been received; act on it.
        """
        if isinstance(msg, OfferAccept):
            return self.offer_accepted()
        elif isinstance(msg, OfferReject):
            return self.offer_rejected()
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
    def subchannel_closed(self):
        """
        The subchannel has been closed
        """

    @m.output()
    def _send_offer(self, offer):
        msg = self._message_encoder(offer)
        self._send_message(msg)

    @m.output()
    def _send_data(self, data):
        # XXX fixme put in data frame
        print("AAAAA")
        self._subchannel.transport.write(data)

    @m.output()
    def _notify_ready(self):
        ##self._ready.callback(None)
        self._on_ready()

    @m.output()
    def _close_input_file(self):
        pass

    @m.output()
    def _send_acknowledge(self):
        # XXX keep a real hash object in here, and put the actual
        # output etc in .. for now, fake
        import os
        msg = FileAcknowledge(0, os.urandom(32))
        self._send_message(msg)

    @m.output()
    def _close_subchannel(self):
        pass

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


    start.upon(
        send_offer,
        enter=permission,
        outputs=[_send_offer],
        collector=_last_one,
    )

    permission.upon(
        offer_accepted,
        enter=sending,
        outputs=[_notify_ready],
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
        outputs=[_close_subchannel, _notify_accepted],
        collector=_last_one,
    )

    closing.upon(
        subchannel_closed,
        enter=closed,
        outputs=[],  # actually, "_notify_accepted" here, probably .. so split "closing" case?
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

    set_trace = getattr(m, "_setTrace", lambda self, f: None)

    def on_message(self, msg):
        """
        A message has been received; act on it.
        """
        if isinstance(msg, FileOffer):
            return self.offer_received(msg)
        elif isinstance(msg, FileData):
            return self.data_received(msg.data)
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
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.input()
    def accept_offer(self, offer):
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

    @m.output()
    def _ask_about_offer(self, offer):
        """
        Use an async callback to ask if this offer should be accepted;
        hook up a "yes" to self.offer_accepted and "no" to
        self.offer_rejected
        """
        self._accept_or_reject(self, offer)

    @m.output()
    def _open_output_file(self, offer):
        self._output = open(offer.filename, "wb")

    @m.output()
    def _close_output_file(self):
        self._output.close()

    @m.output()
    def _send_accept(self, offer):
        msg = OfferAccept()
        self._send_message(msg)

    @m.output()
    def _send_reject(self, offer):
        msg = OfferReject("Offer rejected")  # XXX user-defined message?
        self._send_message(msg)

    @m.output()
    def _send_acknowledge(self):
        # XXX keep a real hash object in here, and put the actual
        # output etc in .. for now, fake
        import os
        msg = FileAcknowledge(0, os.urandom(32))
        self._send_message(msg)

    @m.output()
    def _check_acknowledge(self, acknowledge_msg):
        # XXX check the hash-object against ours
        pass

    @m.output()
    def _close_subchannel(self):
        pass

    @m.output()
    def _write_data_to_file(self, data):
        """
        Writing incoming data to our file
        """
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
        outputs=[_open_output_file, _send_accept],
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
        got_acknowledge,
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

    @m.input()
    def make_offer(self, send_message):
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
    def _create_sender(self, send_message):
        """
        Make a DilatedFileSender
        """
        return DilatedFileSender(send_message)

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
