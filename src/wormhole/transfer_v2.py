from __future__ import absolute_import, print_function, unicode_literals

from typing import Union, Callable

from attr import define, field
from automat import MethodicalMachine
from zope.interface import implementer

from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol, Factory

from . import _interfaces
from ._key import derive_phase_key, encrypt_data


@define
class FileOffer:
    filename: str    # unicode relative pathname
    timestamp: int   # Unix timestamp (seconds since the epoch in GMT)
    bytes: int       # total number of bytes in the file


class DirectoryOffer:
    base: str          # unicode pathname of the root directory (i.e. what the user selected)
    size: int         # total number of bytes in _all_ files
    files: list[str]   # a list containing relative paths for each file


@define
class OfferReject:
    reason: str      # unicode string describing why the offer is rejected


@define
class OfferAccept:
    pass


@define
class Message:
    message: str     # unicode string
    kind: str = "text"


# wormhole: _DeferredWormhole,
def deferred_transfer(wormhole, on_error):
    """
    Do transfer protocol over an async wormhole interface
    """

    control_proto = None

    async def get_control_proto():
        nonlocal control_proto

        # XXX FIXME
        wormhole.allocate_code(2)
        code = await wormhole.get_code()
        print("code", code)

        versions = await wormhole.get_versions()
        print("versions", versions)
        try:
            transfer = versions["transfer"]
        except KeyError:
            # XXX fall back to "classic" file-trasfer
            raise RuntimeError("Peer doesn't support Dilated tranfer")
        mode = transfer.get("mode", None)
        features = transfer.get("features", ())

        if mode not in ["send", "receive", "connect"]:
            raise Exception("protocol error")
        if "basic" not in features:
            raise Exception("protocol error")
        print("versions")
        version = await wormhole.get_versions()
        print(versions)
        print("waiting to dilate")
        endpoints = wormhole.dilate()
        print("got endpoints", endpoints)

        class TransferControl(Protocol):
            def connectionMade(self):
                print("control conneced")

            def dataReceived(self, data):
                print("data: {}".format(data))
        control_proto = await endpoints.control.connect(Factory.forProtocol(TransferControl))

    d = Deferred.fromCoroutine(get_control_proto())
    d.addBoth(print)

    def send_control_message(message):
        print(f"send_control: {message}")

    def send_file_in_offer(offer, on_done):
        print(f"send_file: {offer}")
        on_done()
        return

    def receive_file_in_offer(offer, on_done):
        print(f"receive:file: {offer}")
        on_done()
        return

    transfer = TransferV2(send_control_message, send_file_in_offer, receive_file_in_offer)
    return transfer




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
