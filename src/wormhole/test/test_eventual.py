from twisted.internet.defer import Deferred
from twisted.internet.task import Clock
from twisted.trial import unittest

from unittest import mock

from pytest_twisted import ensureDeferred

from ..eventual import EventualQueue


class IntentionalError(Exception):
    pass


def test_eventually():
    c = Clock()
    eq = EventualQueue(c)
    c1 = mock.Mock()
    eq.eventually(c1, "arg1", "arg2", kwarg1="kw1")
    eq.eventually(c1, "arg3", "arg4", kwarg5="kw5")
    d2 = eq.fire_eventually()
    d3 = eq.fire_eventually("value")
    assert c1.mock_calls == []
    assert not d2.called
    assert not d3.called

    eq.flush_sync()
    assert c1.mock_calls == [
        mock.call("arg1", "arg2", kwarg1="kw1"),
        mock.call("arg3", "arg4", kwarg5="kw5")
    ]
    assert d2.result is None
    assert d3.result == "value"



class _LogObserver:
    """
    Observes the Twisted logs and catches any errors.

    @ivar _errors: A C{list} of L{Failure} instances which were received as
        error events from the Twisted logging system.

    @ivar _added: A C{int} giving the number of times C{_add} has been called
        less the number of times C{_remove} has been called; used to only add
        this observer to the Twisted logging since once, regardless of the
        number of calls to the add method.

    @ivar _ignored: A C{list} of exception types which will not be recorded.
    """

    def __init__(self):
        self._errors = []
        self._added = 0
        self._ignored = []

    def _add(self):
        from twisted.python import log
        if self._added == 0:
            log.addObserver(self.gotEvent)
        self._added += 1

    def _remove(self):
        from twisted.python import log
        self._added -= 1
        if self._added == 0:
            log.removeObserver(self.gotEvent)

    def _ignoreErrors(self, *errorTypes):
        """
        Do not store any errors with any of the given types.
        """
        self._ignored.extend(errorTypes)

    def _clearIgnores(self):
        """
        Stop ignoring any errors we might currently be ignoring.
        """
        self._ignored = []

    def flushErrors(self, *errorTypes):
        """
        Flush errors from the list of caught errors. If no arguments are
        specified, remove all errors. If arguments are specified, only remove
        errors of those types from the stored list.
        """
        if errorTypes:
            flushed = []
            remainder = []
            for f in self._errors:
                if f.check(*errorTypes):
                    flushed.append(f)
                else:
                    remainder.append(f)
            self._errors = remainder
        else:
            flushed = self._errors
            self._errors = []
        return flushed

    def getErrors(self):
        """
        Return a list of errors caught by this observer.
        """
        return self._errors

    def gotEvent(self, event):
        """
        The actual observer method. Called whenever a message is logged.

        @param event: A dictionary containing the log message. Actual
        structure undocumented (see source for L{twisted.python.log}).
        """
        if event.get("isError", False) and "failure" in event:
            f = event["failure"]
            if len(self._ignored) == 0 or not f.check(*self._ignored):
                self._errors.append(f)

lo = _LogObserver()

def test_error():
    lo._add()
    try:
        c = Clock()
        eq = EventualQueue(c)
        c1 = mock.Mock(side_effect=IntentionalError)
        eq.eventually(c1, "arg1", "arg2", kwarg1="kw1")
        assert c1.mock_calls == []

        eq.flush_sync()
        assert c1.mock_calls == [mock.call("arg1", "arg2", kwarg1="kw1")]
    finally:
        lo._remove()

    lo.flushErrors(IntentionalError)


@ensureDeferred
async def test_flush(reactor):
    eq = EventualQueue(reactor)
    d1 = eq.fire_eventually()
    d2 = Deferred()

    def _more(res):
        eq.eventually(d2.callback, None)

    d1.addCallback(_more)
    await eq.flush()
    # d1 will fire, which will queue d2 to fire, and the flush() ought to
    # wait for d2 too
    assert d2.called
