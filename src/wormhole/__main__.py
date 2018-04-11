from __future__ import absolute_import, print_function, unicode_literals
if __name__ == "__main__":
    from .cli import cli
    cli.wormhole()
else:
    # raise ImportError('this module should not be imported')
    pass
