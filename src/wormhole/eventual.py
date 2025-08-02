# inspired-by/adapted-from Foolscap's eventual.py, which Glyph wrote for me
# years ago.

from twisted.internet.defer import Deferred
from twisted.internet.interfaces import IReactorTime
from twisted.python import log


class EventualQueue:
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
        self._calls, to_call = [], self._calls
        for f, args, kwargs in to_call:
            try:
                f(*args, **kwargs)
            except Exception:
                log.err()
        self._timer = None

        # Since the only guidance about semantics is the comment about
        # Foolscap, we make sure that any calls added by the above
        # (i.e. by the calls we just ran) only run in the _next_ turn
        # (as Foolscap does). Not doing this leads to some unexpected
        # dependency of the tests on the precise order things are run
        # in a single turn, which defeats the purpose of this
        # "eventual queue".
        if len(self._calls):
            self._timer = self._clock.callLater(0, self._turn)
        else:
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
