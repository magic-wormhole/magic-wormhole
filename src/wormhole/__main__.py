if __name__ != "__main__":
    raise ImportError('this module should not be imported')


from wormhole.cli import cli


cli.wormhole()
