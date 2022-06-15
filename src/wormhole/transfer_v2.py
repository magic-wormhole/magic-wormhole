from __future__ import absolute_import, print_function, unicode_literals

from typing import Union, Callable

from attr import define, field
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces
from ._key import derive_phase_key, encrypt_data


@define
class Offer:
    id: int          # unique random identifier for this offer
    filename: str    # utf8-encoded unicode relative pathname
    timestamp: int   # Unix timestamp (seconds since the epoch in GMT)
    bytes: int       # total number of bytes in the file
    subchannel: int  # the subchannel which the file will be sent on
    kind: int = 1    # "offer"


@define
class OfferReject:
    id: int          # matching identifier for an existing offer from the other side
    reason: str      # utf8-encoded unicode string describing why the offer is rejected
    kind: int = 2    #  "offer reject"


@define
class OfferAccept:
    id: int          # matching identifier for an existing offer from the other side
    kind: int = 3    #  "offer accpet"


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

    send_control_message: Callable[[Union[Offer, OfferReject, OfferAccept]], None]

    def __attrs_post_init__(self):
        self._queued_offers = []
        self._offers = {}  # id -> Offer
        self._peer_offers = {}  # id -> Offer

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
        self._offers.append(offer)

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
        # XXX async, probably?
        self.send_file_in_offer(offer)
        # ...or another input, about send completed?
        del self._offers[accept.id]

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
        pass

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
