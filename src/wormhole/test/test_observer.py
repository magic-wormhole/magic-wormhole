from twisted.internet.task import Clock
from twisted.python.failure import Failure
import pytest
import pytest_twisted

from ..eventual import EventualQueue
from ..observer import OneShotObserver, SequenceObserver, EmptyableSet


@pytest_twisted.ensureDeferred()
async def test_fire():
    c = Clock()
    eq = EventualQueue(c)
    o = OneShotObserver(eq)
    res = object()
    d1 = o.when_fired()
    eq.flush_sync()
    assert not d1.called
    o.fire(res)
    eq.flush_sync()
    await d1 is res
    d2 = o.when_fired()
    eq.flush_sync()
    await d2 is res
    o.fire_if_not_fired(object())
    eq.flush_sync()

@pytest_twisted.ensureDeferred()
async def test_fire_if_not_fired():
    c = Clock()
    eq = EventualQueue(c)
    o = OneShotObserver(eq)
    res1 = object()
    res2 = object()
    d1 = o.when_fired()
    eq.flush_sync()
    assert not d1.called
    o.fire_if_not_fired(res1)
    o.fire_if_not_fired(res2)
    eq.flush_sync()
    await d1 is res1


@pytest_twisted.ensureDeferred()
async def test_error_before_firing():
    c = Clock()
    eq = EventualQueue(c)
    o = OneShotObserver(eq)
    f = Failure(ValueError("oops"))
    d1 = o.when_fired()
    eq.flush_sync()
    assert not d1.called
    o.error(f)
    eq.flush_sync()
    with pytest.raises(Exception) as ar:
        await d1
    assert ar.value is f.value
    d2 = o.when_fired()
    eq.flush_sync()
    with pytest.raises(Exception) as ar:
        await d2
    assert ar.value is f.value


@pytest_twisted.ensureDeferred()
async def test_error_after_firing():
    c = Clock()
    eq = EventualQueue(c)
    o = OneShotObserver(eq)
    res = object()
    f = Failure(ValueError("oops"))

    o.fire(res)
    eq.flush_sync()
    d1 = o.when_fired()
    o.error(f)
    d2 = o.when_fired()
    eq.flush_sync()
    await d1 is res
    with pytest.raises(Exception) as ar:
        await d2
    assert ar.value is f.value


@pytest_twisted.ensureDeferred()
async def test_fire_multiple():
    c = Clock()
    eq = EventualQueue(c)
    o = SequenceObserver(eq)
    d1 = o.when_next_event()
    eq.flush_sync()
    assert not d1.called
    d2 = o.when_next_event()
    eq.flush_sync()
    assert not d1.called
    assert not d2.called

    ev1 = object()
    ev2 = object()
    o.fire(ev1)
    eq.flush_sync()
    await d1 is ev1
    assert not d2.called

    o.fire(ev2)
    eq.flush_sync()
    await d2 is ev2

    ev3 = object()
    ev4 = object()
    o.fire(ev3)
    o.fire(ev4)

    d3 = o.when_next_event()
    eq.flush_sync()
    await d3 is ev3

    d4 = o.when_next_event()
    eq.flush_sync()
    await d4 is ev4


@pytest_twisted.ensureDeferred()
async def test_error():
    c = Clock()
    eq = EventualQueue(c)
    o = SequenceObserver(eq)
    d1 = o.when_next_event()
    eq.flush_sync()
    assert not d1.called
    f = Failure(ValueError("oops"))
    o.fire(f)
    eq.flush_sync()
    with pytest.raises(Exception) as ar:
        await d1
    assert ar.value is f.value
    d2 = o.when_next_event()
    eq.flush_sync()
    with pytest.raises(Exception) as ar:
        await d2
    assert ar.value is f.value


@pytest_twisted.ensureDeferred()
async def test_set():
    eq = EventualQueue(Clock())
    s = EmptyableSet(_eventual_queue=eq)
    d1 = s.when_next_empty()
    eq.flush_sync()
    assert not d1.called
    s.add(1)
    eq.flush_sync()
    assert not d1.called
    s.add(2)
    s.discard(1)
    d2 = s.when_next_empty()
    eq.flush_sync()
    assert not d1.called
    assert not d2.called
    s.discard(2)
    eq.flush_sync()
    assert await d1 is None
    assert await d2 is None

    s.add(3)
    s.discard(3)
