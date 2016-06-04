from __future__ import print_function, unicode_literals
import sys
from weakref import ref

class ChannelMonitor:
    def __init__(self):
        self._open_channels = set()
    def add(self, w):
        wr = ref(w, self._lost)
        self._open_channels.add(wr)
    def _lost(self, wr):
        print("Error: a Wormhole instance was not closed", file=sys.stderr)
    def close(self, w):
        self._open_channels.discard(ref(w))

monitor = ChannelMonitor() # singleton
