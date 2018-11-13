from __future__ import absolute_import, print_function, unicode_literals
from .cli import cli

if __name__ != "__main__":
    raise ImportError('this module should not be imported')

cli.wormhole()
