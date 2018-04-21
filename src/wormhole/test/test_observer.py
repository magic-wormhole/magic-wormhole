from twisted.internet.task import Clock
from twisted.python.failure import Failure
from twisted.trial import unittest

from ..eventual import EventualQueue
from ..observer import OneShotObserver, SequenceObserver


class OneShot(unittest.TestCase):
    def test_fire(self):
        c = Clock()
        eq = EventualQueue(c)
        o = OneShotObserver(eq)
        res = object()
        d1 = o.when_fired()
        eq.flush_sync()
        self.assertNoResult(d1)
        o.fire(res)
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d1), res)
        d2 = o.when_fired()
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d2), res)
        o.fire_if_not_fired(object())
        eq.flush_sync()

    def test_fire_if_not_fired(self):
        c = Clock()
        eq = EventualQueue(c)
        o = OneShotObserver(eq)
        res1 = object()
        res2 = object()
        d1 = o.when_fired()
        eq.flush_sync()
        self.assertNoResult(d1)
        o.fire_if_not_fired(res1)
        o.fire_if_not_fired(res2)
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d1), res1)

    def test_error_before_firing(self):
        c = Clock()
        eq = EventualQueue(c)
        o = OneShotObserver(eq)
        f = Failure(ValueError("oops"))
        d1 = o.when_fired()
        eq.flush_sync()
        self.assertNoResult(d1)
        o.error(f)
        eq.flush_sync()
        self.assertIdentical(self.failureResultOf(d1), f)
        d2 = o.when_fired()
        eq.flush_sync()
        self.assertIdentical(self.failureResultOf(d2), f)

    def test_error_after_firing(self):
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
        self.assertIdentical(self.successResultOf(d1), res)
        self.assertIdentical(self.failureResultOf(d2), f)


class Sequence(unittest.TestCase):
    def test_fire(self):
        c = Clock()
        eq = EventualQueue(c)
        o = SequenceObserver(eq)
        d1 = o.when_next_event()
        eq.flush_sync()
        self.assertNoResult(d1)
        d2 = o.when_next_event()
        eq.flush_sync()
        self.assertNoResult(d1)
        self.assertNoResult(d2)

        ev1 = object()
        ev2 = object()
        o.fire(ev1)
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d1), ev1)
        self.assertNoResult(d2)

        o.fire(ev2)
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d2), ev2)

        ev3 = object()
        ev4 = object()
        o.fire(ev3)
        o.fire(ev4)

        d3 = o.when_next_event()
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d3), ev3)

        d4 = o.when_next_event()
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d4), ev4)

    def test_error(self):
        c = Clock()
        eq = EventualQueue(c)
        o = SequenceObserver(eq)
        d1 = o.when_next_event()
        eq.flush_sync()
        self.assertNoResult(d1)
        f = Failure(ValueError("oops"))
        o.fire(f)
        eq.flush_sync()
        self.assertIdentical(self.failureResultOf(d1), f)
        d2 = o.when_next_event()
        eq.flush_sync()
        self.assertIdentical(self.failureResultOf(d2), f)
