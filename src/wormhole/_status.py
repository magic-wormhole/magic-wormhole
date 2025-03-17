from attrs import frozen


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
class NoPeer:
    pass


@frozen
class ConnectingPeer:
    last_attempt: int  # unix-timestamp


@frozen
class ReconnectingPeer:
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
    usable. The nameplate has been un-claimed (and may be re-used by a
    different code any time).
    """


# General mailbox statuses
ConnectionStatus = Disconnected | Connecting | Connected | Failed
PeerSharedKey = NoKey | AllegedSharedKey | ConfirmedKey
CodeStatus = NoCode | AllocatedCode | ConsumedCode

# Dilation only
PeerConnection = NoPeer | ConnectingPeer | ConnectedPeer


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
