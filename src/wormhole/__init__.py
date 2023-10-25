from ._rlcompleter import input_with_completion
from .wormhole import create, __version__

__all__ = ["create", "input_with_completion", "__version__"]

from . import _version
__version__ = _version.get_versions()['version']
