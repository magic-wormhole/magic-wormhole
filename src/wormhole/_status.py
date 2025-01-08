from attrs import frozen


@frozen
class Disconnected:
    pass


@frozen
class Connecting:
    url: str
    last_attempt: int  # most-recent second we last tried connecting


@frozen
class Connected:
    url: str


@frozen
class Failed:
    reason: str


@frozen
class NoPeer:
    pass


@frozen
class ConnectingPeer:
    timestamp: int


@frozen
class ReconnectingPeer:
    timestamp: int


@frozen
class ConnectedPeer:
    timestamp: int


@frozen
class NoKey:
    pass


@frozen
class AllegedSharedKey:
    alleged_key: bytes


@frozen
class ConfirmedKey:
    key: bytes



# XXX union types need python 3.10 or later .. but they're nice
# General mailbox statuses
ConnectionStatus = Disconnected | Connecting | Connected | Failed
PeerSharedKey = NoKey | AllegedSharedKey | ConfirmedKey

# Dilation only
PeerConnection = NoPeer | ConnectingPeer | ConnectedPeer


# Q: is there "NeverConnected" versus "Disconnected(last_timestamp)"? Or just "Disconnected"
# (if we're "Disconnected" but also have a PeerSharedKey one could deduce that we did connect at some point? but ...)


@frozen
class WormholeStatus(object):
    """
    Represents the current status of a wormhole for use by the outside
    """

    # are we connected to the Mailbox Server?
    mailbox_connection: ConnectionStatus = Disconnected()

    # only Dilation (or "transit") know if we've actually achived a
    # connection to our peer; this just tracks the PAKE negotiation,
    # basically
    peer_key: PeerSharedKey = NoKey()

    # there's the notion of "we have a mailbox", separate from "a
    # connection". is this worth exposing?
    # when would it fail (without the connection just failing)?
    #  - some server error (but then it would "error" and close connection, no?)
    #  - if we lacked hashcash/permission (also just "error and close"..?)
    #
    # ---> maybe indicates we want a like "failed" state, i.e. we did
    # ---> connect but failed to make progress -- surely we shouldn't
    # ---> just keep re-connecting?


# probably this should "never" reveal secret/sensitive information? on
# the grounds you'll probably show all this to a user somehow/somewhen
@frozen
class DilationStatus(object):
    """
    Represents the current status of a Dilated wormhole

    """
    # Trying to use Dilation definitely implies the existence of a
    # "WormholeStatus" too ... BUT do we actually want to 'embed' the
    # wormhole status like this?

    # are we connected to the Mailbox Server
    mailbox: WormholeStatus

    # current Dilation phase (ever increasing, aka "generation")
    phase: int

    # communication status with peer
    peer_connection: PeerConnection = NoPeer()

    # there's the notion of "we have a shared mailbox", separate from
    # the above; worth revealing?

    # possible other things to reveal here:
    # "hints"? "active_hint"?
    # "are we re-connecting" can be inferred from "mailbox" + "phase"


# worth having an Interface for "there is a new status"? it's just a
# callable that takes a WormholeStatus ... or a DilationStatus ... or
# both? What does an app that wants to just monitor status actually do
# here?


    # def got_wormhole_status(s):
    #     print(f"wormhole: {s}")


    # def got_dilation_status(s):
    #     print(f"  dilation: {s}")

    # w = wormhole.DeferredWormhole(..., status=got_wormhole_status, ...)
    # # ...
    # stuff = w.dilatate(..., status=got_dilation_status, ...)


# so do we provide a "tracker" too??
# dunno, seems like a thing an app can do if it cares about (slash this looks boring:)
# class CurrentStatus:
#     wormhole_status: WormholeStatus
#     dilation_status: DilationStatus
#     def add_status_listener(self, listen):
#         "call listen.update(self) every time either of our things change?"


## maybe we want a like "status manager" to pass around internally?

## that is, something that tracks "wormhole" vs "dilation" updates,
## and calls the appropriate listeners etc
