from __future__ import print_function, unicode_literals

from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.internet.task import Clock
from twisted.trial import unittest

import mock

from ..eventual import EventualQueue


class IntentionalError(Exception):
    pass


class Eventual(unittest.TestCase, object):
    def test_eventually(self):
        c = Clock()
        eq = EventualQueue(c)
        c1 = mock.Mock()
        eq.eventually(c1, "arg1", "arg2", kwarg1="kw1")
        eq.eventually(c1, "arg3", "arg4", kwarg5="kw5")
        d2 = eq.fire_eventually()
        d3 = eq.fire_eventually("value")
        self.assertEqual(c1.mock_calls, [])
        self.assertNoResult(d2)
        self.assertNoResult(d3)

        eq.flush_sync()
        self.assertEqual(c1.mock_calls, [
            mock.call("arg1", "arg2", kwarg1="kw1"),
            mock.call("arg3", "arg4", kwarg5="kw5")
        ])
        self.assertEqual(self.successResultOf(d2), None)
        self.assertEqual(self.successResultOf(d3), "value")

    def test_error(self):
        c = Clock()
        eq = EventualQueue(c)
        c1 = mock.Mock(side_effect=IntentionalError)
        eq.eventually(c1, "arg1", "arg2", kwarg1="kw1")
        self.assertEqual(c1.mock_calls, [])

        eq.flush_sync()
        self.assertEqual(c1.mock_calls,
                         [mock.call("arg1", "arg2", kwarg1="kw1")])

        self.flushLoggedErrors(IntentionalError)

    @inlineCallbacks
    def test_flush(self):
        eq = EventualQueue(reactor)
        d1 = eq.fire_eventually()
        d2 = Deferred()

        def _more(res):
            eq.eventually(d2.callback, None)

        d1.addCallback(_more)
        yield eq.flush()
        # d1 will fire, which will queue d2 to fire, and the flush() ought to
        # wait for d2 too
        self.successResultOf(d2)
