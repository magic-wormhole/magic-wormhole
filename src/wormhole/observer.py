# -*- test-case-name: foolscap.test_observer -*-

# many thanks to AllMyData for contributing the initial version of this code

from twisted.internet import defer
from foolscap import eventual

class OneShotObserverList(object):
    """A one-shot event distributor.

    Subscribers can get a Deferred that will fire with the results of the
    event once it finally occurs. The caller does not need to know whether
    the event has happened yet or not: they get a Deferred in either case.

    The Deferreds returned to subscribers are guaranteed to not fire in the
    current reactor turn; instead, eventually() is used to fire them in a
    later turn. Look at Mark Miller's 'Concurrency Among Strangers' paper on
    erights.org for a description of why this property is useful.

    I can only be fired once."""

    def __init__(self):
        self._fired = False
        self._result = None
        self._watchers = []
        self.__repr__ = self._unfired_repr

    def _unfired_repr(self):
        return "<OneShotObserverList [%s]>" % (self._watchers, )

    def _fired_repr(self):
        return "<OneShotObserverList -> %s>" % (self._result, )

    def whenFired(self):
        if self._fired:
            return eventual.fireEventually(self._result)
        d = defer.Deferred()
        self._watchers.append(d)
        return d

    def fire(self, result):
        assert not self._fired
        self._fired = True
        self._result = result

        for w in self._watchers:
            eventual.eventually(w.callback, result)
        del self._watchers
        self.__repr__ = self._fired_repr

