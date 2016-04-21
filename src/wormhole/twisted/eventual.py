# -*- test-case-name: foolscap.test.test_eventual -*-

from twisted.internet import reactor, defer
from twisted.python import log

class _SimpleCallQueue(object):
    # XXX TODO: merge epsilon.cooperator in, and make this more complete.
    def __init__(self):
        self._events = []
        self._flushObservers = []
        self._timer = None

    def append(self, cb, args, kwargs):
        self._events.append((cb, args, kwargs))
        if not self._timer:
            self._timer = reactor.callLater(0, self._turn)

    def _turn(self):
        self._timer = None
        # flush all the messages that are currently in the queue. If anything
        # gets added to the queue while we're doing this, those events will
        # be put off until the next turn.
        events, self._events = self._events, []
        for cb, args, kwargs in events:
            try:
                cb(*args, **kwargs)
            except:
                log.err()
        if not self._events:
            observers, self._flushObservers = self._flushObservers, []
            for o in observers:
                o.callback(None)

    def flush(self):
        """Return a Deferred that will fire (with None) when the call queue
        is completely empty."""
        if not self._events:
            return defer.succeed(None)
        d = defer.Deferred()
        self._flushObservers.append(d)
        return d


_theSimpleQueue = _SimpleCallQueue()

def eventually(cb, *args, **kwargs):
    """This is the eventual-send operation, used as a plan-coordination
    primitive. The callable will be invoked (with args and kwargs) in a later
    reactor turn. Doing 'eventually(a); eventually(b)' guarantees that a will
    be called before b.

    Any exceptions that occur in the callable will be logged with log.err().
    If you really want to ignore them, be sure to provide a callable that
    catches those exceptions.

    This function returns None. If you care to know when the callable was
    run, be sure to provide a callable that notifies somebody.
    """
    _theSimpleQueue.append(cb, args, kwargs)


def fireEventually(value=None):
    """This returns a Deferred which will fire in a later reactor turn, after
    the current call stack has been completed, and after all other deferreds
    previously scheduled with callEventually().
    """
    d = defer.Deferred()
    eventually(d.callback, value)
    return d

def flushEventualQueue(_ignored=None):
    """This returns a Deferred which fires when the eventual-send queue is
    finally empty. This is useful to wait upon as the last step of a Trial
    test method.
    """
    return _theSimpleQueue.flush()
