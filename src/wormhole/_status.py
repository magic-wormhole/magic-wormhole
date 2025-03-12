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
    hint_description: str


@frozen
class NoKey:
    pass


@frozen
class AllegedSharedKey:
    pass  # DO NOT reveal alleged key here; "status" messages are for users


@frozen
class ConfirmedKey:
    pass  # DO NOT relveal real key here; "status" messages are for users


# General mailbox statuses
ConnectionStatus = Disconnected | Connecting | Connected | Failed
PeerSharedKey = NoKey | AllegedSharedKey | ConfirmedKey

# Dilation only
PeerConnection = NoPeer | ConnectingPeer | ConnectedPeer


# Q: is there "NeverConnected" versus "Disconnected(last_timestamp)"? ...or is "Disconnected" fine?
# (if we're "Disconnected" but also have a PeerSharedKey one could
# deduce that we did connect at some point? but ...)


# NOTE: probably none of the status stuff should ever reveal
# secret/sensitive information -- on the grounds this will probably be
# shown to a all this to a user somehow/somewhen


@frozen
class WormholeStatus(object):
    """
    Represents the current status of a wormhole for use by the outside
    """

    # are we connected to the Mailbox Server?
    mailbox_connection: ConnectionStatus = Disconnected()

    # only Dilation (or "transit") know if we've actually achieved a
    # connection to our peer; this just tracks the PAKE negotiation,
    # basically
    peer_key: PeerSharedKey = NoKey()


@frozen
class DilationStatus(object):
    """
    Represents the current status of a Dilated wormhole

    """
    # If we are using Dilation (or trying to) it definitely means we
    # have a Wormhole, and thus a WormholeStatus too -- but do we
    # actually want to 'embed' the wormhole status like this?

    # status of our connection to the Mailbox Server
    mailbox: WormholeStatus

    # current Dilation generation (ever increasing number)
    generation: int

    # communication status with peer
    peer_connection: PeerConnection = NoPeer()
