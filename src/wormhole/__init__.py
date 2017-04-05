
from ._version import get_versions
__version__ = get_versions()['version']
del get_versions

from .wormhole import create
from ._rlcompleter import input_with_completion

__all__ = ["create", "input_with_completion", "__version__"]
