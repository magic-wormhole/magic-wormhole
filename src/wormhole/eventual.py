# inspired-by/adapted-from Foolscap's eventual.py, which Glyph wrote for me
# years ago.

from twisted.internet.defer import Deferred
from twisted.internet.interfaces import IReactorTime
from twisted.python import log


class EventualQueue(object):
    def __init__(self, clock):
        # pass clock=reactor unless you're testing
        self._clock = IReactorTime(clock)
        self._calls = []
        self._flush_d = None
        self._timer = None

    def eventually(self, f, *args, **kwargs):
        self._calls.append((f, args, kwargs))
        if not self._timer:
            self._timer = self._clock.callLater(0, self._turn)

    def fire_eventually(self, value=None):
        d = Deferred()
        self.eventually(d.callback, value)
        return d

    def _turn(self):
        while self._calls:
            (f, args, kwargs) = self._calls.pop(0)
            try:
                f(*args, **kwargs)
            except Exception:
                log.err()
        self._timer = None
        d, self._flush_d = self._flush_d, None
        if d:
            d.callback(None)

    def flush_sync(self):
        # if you have control over the Clock, this will synchronously flush the
        # queue
        assert self._clock.advance, "needs clock=twisted.internet.task.Clock()"
        while self._calls:
            self._clock.advance(0)

    def flush(self):
        # this is for unit tests, not application code
        assert not self._flush_d, "only one flush at a time"
        self._flush_d = Deferred()
        self.eventually(lambda: None)
        return self._flush_d
