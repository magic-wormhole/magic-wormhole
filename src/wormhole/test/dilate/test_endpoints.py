from __future__ import print_function, unicode_literals
from unittest import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.task import Clock
from twisted.python.failure import Failure
from ..._interfaces import ISubChannel
from ...eventual import EventualQueue
from ..._dilation.subchannel import (ControlEndpoint,
                                     SubchannelConnectorEndpoint,
                                     SubchannelListenerEndpoint,
                                     SubchannelListeningPort,
                                     _WormholeAddress, _SubchannelAddress,
                                     SingleUseEndpointError)
from .common import mock_manager


class CannotDilateError(Exception):
    pass


class Control(unittest.TestCase):
    def test_early_succeed(self):
        # ep.connect() is called before dilation can proceed
        scid0 = 0
        peeraddr = _SubchannelAddress(scid0)
        sc0 = mock.Mock()
        alsoProvides(sc0, ISubChannel)
        eq = EventualQueue(Clock())
        ep = ControlEndpoint(peeraddr, sc0, eq)

        f = mock.Mock()
        p = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        d = ep.connect(f)
        self.assertNoResult(d)

        ep._main_channel_ready()
        eq.flush_sync()

        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(sc0.mock_calls, [mock.call._set_protocol(p),
                                          mock.call._deliver_queued_data()])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(sc0)])

        d = ep.connect(f)
        self.failureResultOf(d, SingleUseEndpointError)

    def test_early_fail(self):
        # ep.connect() is called before dilation is abandoned
        scid0 = 0
        peeraddr = _SubchannelAddress(scid0)
        sc0 = mock.Mock()
        alsoProvides(sc0, ISubChannel)
        eq = EventualQueue(Clock())
        ep = ControlEndpoint(peeraddr, sc0, eq)

        f = mock.Mock()
        p = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        d = ep.connect(f)
        self.assertNoResult(d)

        ep._main_channel_failed(Failure(CannotDilateError()))
        eq.flush_sync()

        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])
        self.assertEqual(sc0.mock_calls, [])

        d = ep.connect(f)
        self.failureResultOf(d, SingleUseEndpointError)

    def test_late_succeed(self):
        # dilation can proceed, then ep.connect() is called
        scid0 = 0
        peeraddr = _SubchannelAddress(scid0)
        sc0 = mock.Mock()
        alsoProvides(sc0, ISubChannel)
        eq = EventualQueue(Clock())
        ep = ControlEndpoint(peeraddr, sc0, eq)

        ep._main_channel_ready()

        f = mock.Mock()
        p = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        d = ep.connect(f)
        eq.flush_sync()
        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(sc0.mock_calls, [mock.call._set_protocol(p),
                                          mock.call._deliver_queued_data()])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(sc0)])

        d = ep.connect(f)
        self.failureResultOf(d, SingleUseEndpointError)

    def test_late_fail(self):
        # dilation is abandoned, then ep.connect() is called
        scid0 = 0
        peeraddr = _SubchannelAddress(scid0)
        sc0 = mock.Mock()
        alsoProvides(sc0, ISubChannel)
        eq = EventualQueue(Clock())
        ep = ControlEndpoint(peeraddr, sc0, eq)

        ep._main_channel_failed(Failure(CannotDilateError()))

        f = mock.Mock()
        p = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        d = ep.connect(f)
        eq.flush_sync()

        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])
        self.assertEqual(sc0.mock_calls, [])

        d = ep.connect(f)
        self.failureResultOf(d, SingleUseEndpointError)


class Endpoints(unittest.TestCase):
    def OFFassert_makeConnection(self, mock_calls):
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(mock_calls[0][0], "makeConnection")
        self.assertEqual(len(mock_calls[0][1]), 1)
        return mock_calls[0][1][0]


class Connector(unittest.TestCase):
    def test_early_succeed(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        peeraddr = _SubchannelAddress(0)
        eq = EventualQueue(Clock())
        ep = SubchannelConnectorEndpoint(m, hostaddr, eq)

        f = mock.Mock()
        p = mock.Mock()
        t = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        with mock.patch("wormhole._dilation.subchannel.SubChannel",
                        return_value=t) as sc:
            d = ep.connect(f)
            eq.flush_sync()
            self.assertNoResult(d)
            ep._main_channel_ready()
            eq.flush_sync()

        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(sc.mock_calls, [mock.call(0, m, hostaddr, peeraddr)])
        self.assertEqual(t.mock_calls, [mock.call._set_protocol(p)])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(t)])

    def test_early_fail(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelConnectorEndpoint(m, hostaddr, eq)

        f = mock.Mock()
        p = mock.Mock()
        t = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        with mock.patch("wormhole._dilation.subchannel.SubChannel",
                        return_value=t) as sc:
            d = ep.connect(f)
            eq.flush_sync()
            self.assertNoResult(d)
            ep._main_channel_failed(Failure(CannotDilateError()))
            eq.flush_sync()

        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])
        self.assertEqual(sc.mock_calls, [])
        self.assertEqual(t.mock_calls, [])

    def test_late_succeed(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        peeraddr = _SubchannelAddress(0)
        eq = EventualQueue(Clock())
        ep = SubchannelConnectorEndpoint(m, hostaddr, eq)
        ep._main_channel_ready()

        f = mock.Mock()
        p = mock.Mock()
        t = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        with mock.patch("wormhole._dilation.subchannel.SubChannel",
                        return_value=t) as sc:
            d = ep.connect(f)
            eq.flush_sync()

        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(sc.mock_calls, [mock.call(0, m, hostaddr, peeraddr)])
        self.assertEqual(t.mock_calls, [mock.call._set_protocol(p)])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(t)])

    def test_late_fail(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelConnectorEndpoint(m, hostaddr, eq)
        ep._main_channel_failed(Failure(CannotDilateError()))

        f = mock.Mock()
        p = mock.Mock()
        t = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        with mock.patch("wormhole._dilation.subchannel.SubChannel",
                        return_value=t) as sc:
            d = ep.connect(f)
            eq.flush_sync()

        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])
        self.assertEqual(sc.mock_calls, [])
        self.assertEqual(t.mock_calls, [])


class Listener(unittest.TestCase):
    def test_early_succeed(self):
        # listen, main_channel_ready, got_open, got_open
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelListenerEndpoint(m, hostaddr, eq)

        f = mock.Mock()
        p1 = mock.Mock()
        p2 = mock.Mock()
        f.buildProtocol = mock.Mock(side_effect=[p1, p2])

        d = ep.listen(f)
        eq.flush_sync()
        self.assertNoResult(d)
        self.assertEqual(f.buildProtocol.mock_calls, [])

        ep._main_channel_ready()
        eq.flush_sync()
        lp = self.successResultOf(d)
        self.assertIsInstance(lp, SubchannelListeningPort)

        self.assertEqual(lp.getHost(), hostaddr)
        # TODO: IListeningPort says we must provide this, but I don't know
        # that anyone would ever call it.
        lp.startListening()

        t1 = mock.Mock()
        peeraddr1 = _SubchannelAddress(1)
        ep._got_open(t1, peeraddr1)

        self.assertEqual(t1.mock_calls, [mock.call._set_protocol(p1),
                                         mock.call._deliver_queued_data()])
        self.assertEqual(p1.mock_calls, [mock.call.makeConnection(t1)])
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr1)])

        t2 = mock.Mock()
        peeraddr2 = _SubchannelAddress(2)
        ep._got_open(t2, peeraddr2)

        self.assertEqual(t2.mock_calls, [mock.call._set_protocol(p2),
                                         mock.call._deliver_queued_data()])
        self.assertEqual(p2.mock_calls, [mock.call.makeConnection(t2)])
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr1),
                                                      mock.call(peeraddr2)])

        lp.stopListening()  # TODO: should this do more?

    def test_early_fail(self):
        # listen, main_channel_fail
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelListenerEndpoint(m, hostaddr, eq)

        f = mock.Mock()
        p1 = mock.Mock()
        p2 = mock.Mock()
        f.buildProtocol = mock.Mock(side_effect=[p1, p2])

        d = ep.listen(f)
        eq.flush_sync()
        self.assertNoResult(d)

        ep._main_channel_failed(Failure(CannotDilateError()))
        eq.flush_sync()
        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])

    def test_late_succeed(self):
        # main_channel_ready, got_open, listen, got_open
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelListenerEndpoint(m, hostaddr, eq)
        ep._main_channel_ready()

        f = mock.Mock()
        p1 = mock.Mock()
        p2 = mock.Mock()
        f.buildProtocol = mock.Mock(side_effect=[p1, p2])

        t1 = mock.Mock()
        peeraddr1 = _SubchannelAddress(1)
        ep._got_open(t1, peeraddr1)
        eq.flush_sync()

        self.assertEqual(t1.mock_calls, [])
        self.assertEqual(p1.mock_calls, [])

        d = ep.listen(f)
        eq.flush_sync()
        lp = self.successResultOf(d)
        self.assertIsInstance(lp, SubchannelListeningPort)
        self.assertEqual(lp.getHost(), hostaddr)
        lp.startListening()

        # TODO: assert makeConnection is called *before* _deliver_queued_data
        self.assertEqual(t1.mock_calls, [mock.call._set_protocol(p1),
                                         mock.call._deliver_queued_data()])
        self.assertEqual(p1.mock_calls, [mock.call.makeConnection(t1)])
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr1)])

        t2 = mock.Mock()
        peeraddr2 = _SubchannelAddress(2)
        ep._got_open(t2, peeraddr2)

        self.assertEqual(t2.mock_calls, [mock.call._set_protocol(p2),
                                         mock.call._deliver_queued_data()])
        self.assertEqual(p2.mock_calls, [mock.call.makeConnection(t2)])
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr1),
                                                      mock.call(peeraddr2)])

        lp.stopListening()  # TODO: should this do more?

    def test_late_fail(self):
        # main_channel_fail, listen
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=0)
        hostaddr = _WormholeAddress()
        eq = EventualQueue(Clock())
        ep = SubchannelListenerEndpoint(m, hostaddr, eq)
        ep._main_channel_failed(Failure(CannotDilateError()))

        f = mock.Mock()
        p1 = mock.Mock()
        p2 = mock.Mock()
        f.buildProtocol = mock.Mock(side_effect=[p1, p2])

        d = ep.listen(f)
        eq.flush_sync()
        self.failureResultOf(d).check(CannotDilateError)
        self.assertEqual(f.buildProtocol.mock_calls, [])
