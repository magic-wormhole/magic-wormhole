from ._rlcompleter import input_with_completion
from .wormhole import create
from ._status import (  # export as public API
    WormholeStatus, DilationStatus,
    ConnectionStatus, PeerSharedKey, CodeStatus, PeerConnection,
    Disconnected, Connecting, Connected, Failed, Closed,
    NoPeer, ConnectingPeer, ConnectedPeer, ReconnectingPeer, StoppedPeer,
    NoKey, AllegedSharedKey, ConfirmedKey,
    NoCode, AllocatedCode, ConsumedCode,
)
from ._dilation.subchannel import SubchannelAddress

from . import _version
__version__ = _version.get_versions()['version']

__all__ = [
    "__version__",
    "create", "input_with_completion",
    "WormholeStatus",
    "ConnectionStatus", "PeerSharedKey", "CodeStatus", "PeerConnection",
    "Disconnected", "Connecting", "Connected", "Failed", "Closed",
    "NoPeer", "ConnectingPeer", "ConnectedPeer", "ReconnectingPeer", "StoppedPeer",
    "NoKey", "AllegedSharedKey", "ConfirmedKey",
    "NoCode", "AllocatedCode", "ConsumedCode",

    # Dilation-related exports
    "DilationStatus",
    "SubchannelAddress",
]
