from __future__ import print_function, unicode_literals
import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from ..._interfaces import ISubChannel
from ..._dilation.subchannel import (ControlEndpoint,
                                     SubchannelConnectorEndpoint,
                                     SubchannelListenerEndpoint,
                                     SubchannelListeningPort,
                                     _WormholeAddress, _SubchannelAddress,
                                     SingleUseEndpointError)
from .common import mock_manager

class Endpoints(unittest.TestCase):
    def test_control(self):
        scid0 = b"scid0"
        peeraddr = _SubchannelAddress(scid0)
        ep = ControlEndpoint(peeraddr)

        f = mock.Mock()
        p = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        d = ep.connect(f)
        self.assertNoResult(d)

        t = mock.Mock()
        alsoProvides(t, ISubChannel)
        ep._subchannel_zero_opened(t)
        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(t.mock_calls, [mock.call._set_protocol(p)])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(t)])

        d = ep.connect(f)
        self.failureResultOf(d, SingleUseEndpointError)

    def assert_makeConnection(self, mock_calls):
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(mock_calls[0][0], "makeConnection")
        self.assertEqual(len(mock_calls[0][1]), 1)
        return mock_calls[0][1][0]

    def test_connector(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=b"scid")
        hostaddr = _WormholeAddress()
        peeraddr = _SubchannelAddress(b"scid")
        ep = SubchannelConnectorEndpoint(m, hostaddr)

        f = mock.Mock()
        p = mock.Mock()
        t = mock.Mock()
        f.buildProtocol = mock.Mock(return_value=p)
        with mock.patch("wormhole._dilation.subchannel.SubChannel",
                        return_value=t) as sc:
            d = ep.connect(f)
        self.assertIdentical(self.successResultOf(d), p)
        self.assertEqual(f.buildProtocol.mock_calls, [mock.call(peeraddr)])
        self.assertEqual(sc.mock_calls, [mock.call(b"scid", m, hostaddr, peeraddr)])
        self.assertEqual(t.mock_calls, [mock.call._set_protocol(p)])
        self.assertEqual(p.mock_calls, [mock.call.makeConnection(t)])

    def test_listener(self):
        m = mock_manager()
        m.allocate_subchannel_id = mock.Mock(return_value=b"scid")
        hostaddr = _WormholeAddress()
        ep = SubchannelListenerEndpoint(m, hostaddr)

        f = mock.Mock()
        p1 = mock.Mock()
        p2 = mock.Mock()
        f.buildProtocol = mock.Mock(side_effect=[p1, p2])

        # OPEN that arrives before we ep.listen() should be queued

        t1 = mock.Mock()
        peeraddr1 = _SubchannelAddress(b"peer1")
        ep._got_open(t1, peeraddr1)

        d = ep.listen(f)
        lp = self.successResultOf(d)
        self.assertIsInstance(lp, SubchannelListeningPort)

        self.assertEqual(lp.getHost(), hostaddr)
        lp.startListening()

        self.assertEqual(t1.mock_calls, [mock.call._set_protocol(p1)])
        self.assertEqual(p1.mock_calls, [mock.call.makeConnection(t1)])

        t2 = mock.Mock()
        peeraddr2 = _SubchannelAddress(b"peer2")
        ep._got_open(t2, peeraddr2)

        self.assertEqual(t2.mock_calls, [mock.call._set_protocol(p2)])
        self.assertEqual(p2.mock_calls, [mock.call.makeConnection(t2)])

        lp.stopListening() # TODO: should this do more?
