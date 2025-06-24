from attrs import frozen, Factory
from typing import List


@frozen
class Disconnected:
    pass


@frozen
class Connecting:
    url: str
    last_attempt: int  # unix-timestamp when we last tried connecting


@frozen
class Connected:
    url: str


@frozen
class Failed:
    reason: str


@frozen
class Closed:
    """
    We have purposely shut down our connection to the server
    """


@frozen
class NoPeer:
    """
    We have yet to see a peer
    """


@frozen
class StoppedPeer:
    """
    We have disconnected from the peer (probably on purpose)
    """


@frozen
class ConnectingPeer:
    """
    We are actively trying to connect to a peer
    """
    last_attempt: int  # unix-timestamp


@frozen
class ReconnectingPeer:
    """
    We are actively trying to connect to a peer.
    In contract to `ConnectingPeer`, we've already reached our peer at least once.
    """
    last_attempt: int  # unix-timestamp


@frozen
class ConnectedPeer:
    connected_at: int  # when we first connected (seconds since epoch)
    expires_at: int  # earliest we will consider re-connecting (seconds since epoch)
    hint_description: str  # what sort of connection is this?


@frozen
class NoKey:
    pass


@frozen
class AllegedSharedKey:
    pass  # DO NOT reveal alleged key here; "status" messages are for users


@frozen
class ConfirmedKey:
    pass  # DO NOT reveal real key here; "status" messages are for users


@frozen
class NoCode:
    """
    Not allocated yet
    """


@frozen
class AllocatedCode:
    """
    A valid code is available
    """


@frozen
class ConsumedCode:
    """
    The code was used by the other side, and is now no longer
    usable. The nameplate has been un-claimed (and may be reused by a
    different code any time).
    """


# note that the tests (e.g. test_api.py) leverage the order of these
# types inside the Unions, so that we don't have to separately
# enumerate the expected order (and then also ensure we don't miss any)


# General mailbox statuses
ConnectionStatus = Disconnected | Connecting | Connected | Failed | Closed
PeerSharedKey = NoKey | AllegedSharedKey | ConfirmedKey
CodeStatus = NoCode | AllocatedCode | ConsumedCode

# Dilation only
PeerConnection = NoPeer | ConnectingPeer | ConnectedPeer | ReconnectingPeer | StoppedPeer


# NOTE: probably none of the status stuff should ever reveal secret or
# sensitive information -- on the grounds all this will probably be
# shown to a user at some point


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

    # we don't reveal the actual code here, on the theory the UI
    # should already know it and/or be displaying it somehow. This
    # communicates the *status* of that code, e.g. whether it is stale
    # or not.
    code: CodeStatus = NoCode()


@frozen
class DilationHint:
    url: str
    is_direct: bool


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

    # available methods to get to peer
    hints: List[DilationHint] = Factory(list)
