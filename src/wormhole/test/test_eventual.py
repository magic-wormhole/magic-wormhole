from twisted.internet.defer import Deferred
from twisted.internet.task import Clock

from unittest import mock

from pytest_twisted import ensureDeferred

from ..eventual import EventualQueue


class IntentionalError(Exception):
    pass


def test_eventually(observe_errors):
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


def test_error(observe_errors):
    c = Clock()
    eq = EventualQueue(c)
    c1 = mock.Mock(side_effect=IntentionalError)
    eq.eventually(c1, "arg1", "arg2", kwarg1="kw1")
    assert c1.mock_calls == []

    eq.flush_sync()
    assert c1.mock_calls == [mock.call("arg1", "arg2", kwarg1="kw1")]
    observe_errors.flush(IntentionalError)


@ensureDeferred
async def test_flush(reactor, observe_errors):
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
