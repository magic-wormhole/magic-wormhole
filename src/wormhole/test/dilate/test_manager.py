from __future__ import print_function, unicode_literals
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.defer import Deferred
from twisted.internet.task import Clock, Cooperator
import mock
from ...eventual import EventualQueue
from ..._interfaces import ISend, IDilationManager
from ...util import dict_to_bytes
from ..._dilation.manager import (Dilator,
                                  OldPeerCannotDilateError,
                                  UnknownDilationMessageType)
from ..._dilation.subchannel import _WormholeAddress
from .common import clear_mock_calls


def make_dilator():
    reactor = object()
    clock = Clock()
    eq = EventualQueue(clock)
    term = mock.Mock(side_effect=lambda: True)  # one write per Eventual tick

    def term_factory():
        return term
    coop = Cooperator(terminationPredicateFactory=term_factory,
                      scheduler=eq.eventually)
    send = mock.Mock()
    alsoProvides(send, ISend)
    dil = Dilator(reactor, eq, coop)
    dil.wire(send)
    return dil, send, reactor, eq, clock, coop


class TestDilator(unittest.TestCase):
    def test_leader(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        d2 = dil.dilate()
        self.assertNoResult(d1)
        self.assertNoResult(d2)

        key = b"key"
        transit_key = object()
        with mock.patch("wormhole._dilation.manager.derive_key",
                        return_value=transit_key) as dk:
            dil.got_key(key)
        self.assertEqual(dk.mock_calls, [mock.call(key, b"dilation-v1", 32)])
        self.assertIdentical(dil._transit_key, transit_key)
        self.assertNoResult(d1)
        self.assertNoResult(d2)

        m = mock.Mock()
        alsoProvides(m, IDilationManager)
        m.when_first_connected.return_value = wfc_d = Deferred()
        # TODO: test missing can-dilate, and no-overlap
        with mock.patch("wormhole._dilation.manager.Manager",
                        return_value=m) as ml:
            with mock.patch("wormhole._dilation.manager.make_side",
                            return_value="us"):
                dil.got_wormhole_versions({"can-dilate": ["1"]})
            # that should create the Manager. Because "us" > "them", we're
            # the leader
        self.assertEqual(ml.mock_calls, [mock.call(send, "us", transit_key,
                                                   None, reactor, eq, coop)])
        self.assertEqual(m.mock_calls, [mock.call.start(),
                                        mock.call.when_first_connected(),
                                        ])
        clear_mock_calls(m)
        self.assertNoResult(d1)
        self.assertNoResult(d2)

        host_addr = _WormholeAddress()
        m_wa = mock.patch("wormhole._dilation.manager._WormholeAddress",
                          return_value=host_addr)
        peer_addr = object()
        m_sca = mock.patch("wormhole._dilation.manager._SubchannelAddress",
                           return_value=peer_addr)
        ce = mock.Mock()
        m_ce = mock.patch("wormhole._dilation.manager.ControlEndpoint",
                          return_value=ce)
        sc = mock.Mock()
        m_sc = mock.patch("wormhole._dilation.manager.SubChannel",
                          return_value=sc)

        lep = object()
        m_sle = mock.patch("wormhole._dilation.manager.SubchannelListenerEndpoint",
                           return_value=lep)

        with m_wa, m_sca, m_ce as m_ce_m, m_sc as m_sc_m, m_sle as m_sle_m:
            wfc_d.callback(None)
            eq.flush_sync()
        scid0 = b"\x00\x00\x00\x00"
        self.assertEqual(m_ce_m.mock_calls, [mock.call(peer_addr)])
        self.assertEqual(m_sc_m.mock_calls,
                         [mock.call(scid0, m, host_addr, peer_addr)])
        self.assertEqual(ce.mock_calls, [mock.call._subchannel_zero_opened(sc)])
        self.assertEqual(m_sle_m.mock_calls, [mock.call(m, host_addr)])
        self.assertEqual(m.mock_calls,
                         [mock.call.set_subchannel_zero(scid0, sc),
                          mock.call.set_listener_endpoint(lep),
                          ])
        clear_mock_calls(m)

        eps = self.successResultOf(d1)
        self.assertEqual(eps, self.successResultOf(d2))
        d3 = dil.dilate()
        eq.flush_sync()
        self.assertEqual(eps, self.successResultOf(d3))

        self.assertEqual(m.mock_calls, [])
        dil.received_dilate(dict_to_bytes(dict(type="please")))
        self.assertEqual(m.mock_calls, [mock.call.rx_PLEASE()])
        clear_mock_calls(m)

        hintmsg = dict(type="connection-hints")
        dil.received_dilate(dict_to_bytes(hintmsg))
        self.assertEqual(m.mock_calls, [mock.call.rx_HINTS(hintmsg)])
        clear_mock_calls(m)

        dil.received_dilate(dict_to_bytes(dict(type="dilate")))
        self.assertEqual(m.mock_calls, [mock.call.rx_DILATE()])
        clear_mock_calls(m)

        dil.received_dilate(dict_to_bytes(dict(type="unknown")))
        self.assertEqual(m.mock_calls, [])
        self.flushLoggedErrors(UnknownDilationMessageType)

    def test_follower(self):
        # todo: this no longer proceeds far enough to pick a side
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        self.assertNoResult(d1)
        self.assertEqual(send.mock_calls, [])

        key = b"key"
        transit_key = object()
        with mock.patch("wormhole._dilation.manager.derive_key",
                        return_value=transit_key):
            dil.got_key(key)

        m = mock.Mock()
        alsoProvides(m, IDilationManager)
        m.when_first_connected.return_value = Deferred()
        with mock.patch("wormhole._dilation.manager.Manager", return_value=m) as mf:
            with mock.patch("wormhole._dilation.manager.make_side",
                            return_value="me"):
                dil.got_wormhole_versions({"can-dilate": ["1"]})
        # we want to dilate (dil.dilate() above), and now we know they *can*
        # dilate (got_wormhole_versions), so we create and start the manager
        self.assertEqual(mf.mock_calls, [mock.call(send, "me", transit_key,
                                                   None, reactor, eq, coop)])
        self.assertEqual(m.mock_calls, [mock.call.start(),
                                        mock.call.when_first_connected(),
                                        ])

    def test_peer_cannot_dilate(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        self.assertNoResult(d1)

        dil.got_wormhole_versions({})  # missing "can-dilate"
        eq.flush_sync()
        f = self.failureResultOf(d1)
        f.check(OldPeerCannotDilateError)

    def test_disjoint_versions(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        self.assertNoResult(d1)

        dil.got_wormhole_versions({"can-dilate": [-1]})
        eq.flush_sync()
        f = self.failureResultOf(d1)
        f.check(OldPeerCannotDilateError)

    def test_early_dilate_messages(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        dil._transit_key = b"key"
        d1 = dil.dilate()
        self.assertNoResult(d1)
        dil.received_dilate(dict_to_bytes(dict(type="please")))
        hintmsg = dict(type="connection-hints")
        dil.received_dilate(dict_to_bytes(hintmsg))

        m = mock.Mock()
        alsoProvides(m, IDilationManager)
        m.when_first_connected.return_value = Deferred()

        with mock.patch("wormhole._dilation.manager.Manager",
                        return_value=m) as ml:
            with mock.patch("wormhole._dilation.manager.make_side",
                            return_value="us"):
                dil.got_wormhole_versions({"can-dilate": ["1"]})
        self.assertEqual(ml.mock_calls, [mock.call(send, "us", b"key",
                                                   None, reactor, eq, coop)])
        self.assertEqual(m.mock_calls, [mock.call.start(),
                                        mock.call.rx_PLEASE(),
                                        mock.call.rx_HINTS(hintmsg),
                                        mock.call.when_first_connected()])

    def test_transit_relay(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        dil._transit_key = b"key"
        relay = object()
        d1 = dil.dilate(transit_relay_location=relay)
        self.assertNoResult(d1)

        with mock.patch("wormhole._dilation.manager.Manager") as ml:
            with mock.patch("wormhole._dilation.manager.make_side",
                            return_value="us"):
                dil.got_wormhole_versions({"can-dilate": ["1"]})
        self.assertEqual(ml.mock_calls, [mock.call(send, "us", b"key",
                                                   relay, reactor, eq, coop),
                                         mock.call().start(),
                                         mock.call().when_first_connected()])
