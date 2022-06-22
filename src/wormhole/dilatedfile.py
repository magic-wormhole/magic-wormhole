
# prototype state-machine "diagrams" / skeleton code for Dilated File
# Transfer

from attr import define, field
from automat import MethodicalMachine


@define
class DilatedFileReceiver:
    """
    Manages the receiving of a single FileOffer
    """
    m = MethodicalMachine()

    @m.state(initial=True)
    def start(self):
        """
        Initial state
        """

    @m.state()
    def mode_ask(self):
        """
        Ask permissions
        """

    @m.state()
    def mode_yes(self):
        """
        Immediately stream
        """

    @m.state()
    def permission(self):
        """
        Waiting for a yes/no about an offer
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
    def open_mode_ask(self, subchannel):
        """
        Open in 'ask' mode
        """

    @m.input()
    def open_mode_yes(self, subchannel):
        """
        Open in 'yes' mode
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
    def data_finished(self):
        """
        All expected data is received
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.output()
    def _remember_subchannel(self, subchannel):
        self._subchannel = subchannel
        # hook up "it closed" to .subchannel_closed
        # hook up "got message" to .. something

    @m.output()
    def _ask_about_offer(self, offer):
        """
        Use an async callback to ask if this offer should be accepted;
        hook up a "yes" to self.offer_accepted and "no" to
        self.offer_rejected
        """

    @m.output()
    def _open_output_file(self):
        pass

    @m.output()
    def _close_output_file(self):
        pass

    @m.output()
    def _send_accept(self):
        pass

    @m.output()
    def _send_reject(self):
        pass

    @m.output()
    def _close_subchannel(self):
        pass

    @m.output()
    def _write_data_to_file(self, data):
        """
        Writing incoming data to our file
        """

    @m.output()
    def _check_remaining(self, data):
        """
        Are we done?
        """
        # if "got all the bytes" then inject self.data_finished

    start.upon(
        open_mode_ask,
        enter=mode_ask,
        outputs=[_remember_subchannel],
    )
    start.upon(
        open_mode_yes,
        enter=mode_yes,
        outputs=[_remember_subchannel],
    )

    mode_ask.upon(
        offer_received,
        enter=permission,
        outputs=[_ask_about_offer],
    )

    permission.upon(
        accept_offer,
        enter=receive_data,
        outputs=[_open_output_file, _send_accept],
    )
    permission.upon(
        reject_offer,
        enter=closing,
        outputs=[_send_reject, _close_subchannel],
    )

    mode_yes.upon(
        offer_received,
        enter=receive_data,
        outputs=[_open_output_file],
    )

    receive_data.upon(
        data_received,
        enter=receive_data,
        outputs=[_write_data_to_file, _check_remaining],
    )
    receive_data.upon(
        data_finished,
        enter=closing,
        outputs=[_close_output_file, _close_subchannel],
    )

    closing.upon(
        subchannel_closed,
        enter=closed,
        outputs=[],
    )


@define
class DilatedFileSender:
    """
    Manages the sending of a single file
    """
    m = MethodicalMachine()

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
    def open_mode_ask(self, subchannel, offer):
        """
        Open in 'ask' mode
        """

    @m.input()
    def open_mode_yes(self, subchannel, offer):
        """
        Open in 'yes' mode
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.input()
    def offer_accepted(self, offer):
        """
        The peer has accepted our offer
        """

    @m.input()
    def offer_rejected(self, offer):
        """
        The peer has rejected our offer
        """

    @m.input()
    def subchannel_closed(self):
        """
        The subchannel has closed
        """

    @m.input()
    def send_data(self):
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
    def _remember_subchannel(self, subchannel):
        self._subchannel = subchannel
        # hook up "it closed" to .subchannel_closed
        # hook up "got message" to .. something

    @m.output()
    def _send_offer(self, offer):
        pass

    @m.output()
    def _close_input_file(self):
        pass

    @m.output()
    def _close_subchannel(self):
        pass

    @m.output()
    def _send_some_data(self):
        """
        If we have data remaining, send it.
        """
        # after one reactor turn, recurse:
        # - if data, _send_some_data again
        # - otherwise data_finished

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
        open_mode_ask,
        enter=permission,
        outputs=[_remember_subchannel, _send_offer],
    )
    start.upon(
        open_mode_yes,
        enter=sending,
        outputs=[_remember_subchannel, _send_offer, _send_some_data],
    )

    permission.upon(
        offer_accepted,
        enter=sending,
        outputs=[_send_some_data],
    )
    permission.upon(
        offer_rejected,
        enter=closing,
        outputs=[_close_input_file, _close_subchannel, _notify_rejected],
    )

    sending.upon(
        send_data,
        enter=sending,
        outputs=[_send_some_data],
    )
    sending.upon(
        data_finished,
        enter=closing,
        outputs=[_close_input_file, _close_subchannel, _notify_accepted],
    )

    closing.upon(
        subchannel_closed,
        enter=closed,
        outputs=[],  # actually, "_notify_accepted" here, probably .. so split "closing" case?
    )




@define
class DilatedFileTransfer(object):
    """
    Manages transfers for the Dilated File Transfer protocol
    """
    m = MethodicalMachine()

    def got_peer_versions(self, versions):
        if versions["mode"] == "send":
            self.peer_send()
        elif versions["mode"] == "receive":
            self.peer_receive()
        elif versions["mode"] == "connect":
            self.peer_connect()

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
    def peer_send():
        """
        Peer is in mode 'send'
        """

    @m.input()
    def peer_receive():
        """
        Peer is in mode 'receive'
        """

    @m.input()
    def peer_connect():
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
    def send_offer(self, offer):
        """
        Make an offer to the other peer.
        """

    @m.input()
    def offer_received(self, offer):
        """
        The peer has made an offer to us.
        """

    @m.input()
    def dilation_closed(self):
        """
        The dilated connection has been closed down
        """

    @m.output()
    def _create_receiver(self, offer):
        """
        Make a DilatedFileReceiver
        """

    @m.output()
    def _create_sender(self, offer):
        """
        Make a DilatedFileSender
        """

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
        outputs=[]
    )

    dilated.upon(
        peer_send,
        enter=receiving,
        outputs=[]
    )

    dilated.upon(
        peer_connect,
        enter=connect,
        outputs=[],
    )

    sending.upon(
        send_offer,
        enter=sending,
        outputs=[_create_sender],
    )
    sending.upon(
        offer_received,
        enter=closing,
        outputs=[_protocol_error, _close_dilation],
    )
    sending.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_outgoing],
    )

    receiving.upon(
        offer_received,
        enter=receiving,
        outputs=[_create_receiver],
    )
    receiving.upon(
        send_offer,
        enter=closing,
        outputs=[_protocol_error, _close_dilation],
    )
    receiving.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_incoming],
    )

    connect.upon(
        offer_received,
        enter=connect,
        outputs=[_create_receiver],
    )
    connect.upon(
        send_offer,
        enter=connect,
        outputs=[_create_sender],
    )
    connect.upon(
        dilation_closed,
        enter=closed,
        outputs=[_cleanup_outgoing, _cleanup_incoming],
    )

    closing.upon(
        dilation_closed,
        enter=closed,
        outputs=[],
    )
