from ._rlcompleter import input_with_completion
from .wormhole import create

from . import _version
__version__ = _version.get_versions()['version']

__all__ = ["create", "input_with_completion", "__version__"]
