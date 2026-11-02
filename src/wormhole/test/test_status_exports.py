import wormhole
from wormhole import _status

NAMES = [
    "ConnectionStatus", "PeerSharedKey", "CodeStatus", "PeerConnection",
    "Disconnected", "Connecting", "Connected", "Failed", "Closed",
    "NoPeer", "ConnectingPeer", "ConnectedPeer", "ReconnectingPeer", "StoppedPeer",
    "NoKey", "AllegedSharedKey", "ConfirmedKey",
    "NoCode", "AllocatedCode", "ConsumedCode",
]


def test_union_types_and_members_are_public():
    for name in NAMES:
        assert getattr(wormhole, name) is getattr(_status, name), name
        assert name in wormhole.__all__, name
