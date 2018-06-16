from __future__ import print_function, unicode_literals

from twisted.internet.defer import Deferred
from twisted.python.failure import Failure

NoResult = object()


class OneShotObserver(object):
    def __init__(self, eventual_queue):
        self._eq = eventual_queue
        self._result = NoResult
        self._observers = []  # list of Deferreds

    def when_fired(self):
        d = Deferred()
        self._observers.append(d)
        self._maybe_call_observers()
        return d

    def fire(self, result):
        assert self._result is NoResult
        self._result = result
        self._maybe_call_observers()

    def _maybe_call_observers(self):
        if self._result is NoResult:
            return
        observers, self._observers = self._observers, []
        for d in observers:
            self._eq.eventually(d.callback, self._result)

    def error(self, f):
        # errors will override an existing result
        assert isinstance(f, Failure)
        self._result = f
        self._maybe_call_observers()

    def fire_if_not_fired(self, result):
        if self._result is NoResult:
            self.fire(result)


class SequenceObserver(object):
    def __init__(self, eventual_queue):
        self._eq = eventual_queue
        self._error = None
        self._results = []
        self._observers = []

    def when_next_event(self):
        d = Deferred()
        if self._error:
            self._eq.eventually(d.errback, self._error)
        elif self._results:
            result = self._results.pop(0)
            self._eq.eventually(d.callback, result)
        else:
            self._observers.append(d)
        return d

    def fire(self, result):
        if isinstance(result, Failure):
            self._error = result
            for d in self._observers:
                self._eq.eventually(d.errback, self._error)
            self._observers = []
        else:
            self._results.append(result)
            if self._observers:
                d = self._observers.pop(0)
                self._eq.eventually(d.callback, self._results.pop(0))
