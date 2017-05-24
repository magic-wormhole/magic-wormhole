if __name__ != "__main__":
    raise ImportError('this module should not be imported')


from .cli import cli


cli.wormhole()
