from ._rlcompleter import input_with_completion
from .wormhole import create
from ._status import WormholeStatus, DilationStatus  # export as public API
from ._dilation.subchannel import SubchannelAddress

from . import _version
__version__ = _version.get_versions()['version']

__all__ = [
    "__version__",
    "create", "input_with_completion",
    "WormholeStatus",

    # Dilation-related exports
    "DilationStatus",
    "SubchannelAddress",
]
