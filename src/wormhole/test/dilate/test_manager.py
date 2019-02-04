from __future__ import print_function, unicode_literals
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.defer import Deferred
from twisted.internet.task import Clock, Cooperator
import mock
from ...eventual import EventualQueue
from ..._interfaces import ISend, IDilationManager
from ...util import dict_to_bytes
from ..._dilation import roles
from ..._dilation.encode import to_be4
from ..._dilation.manager import (Dilator, Manager, make_side,
                                  OldPeerCannotDilateError,
                                  UnknownDilationMessageType,
                                  UnexpectedKCM,
                                  UnknownMessageType)
from ..._dilation.subchannel import _WormholeAddress
from ..._dilation.connection import Open, Data, Close, Ack, KCM, Ping, Pong
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
    def test_manager_and_endpoints(self):
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
        with mock.patch("wormhole._dilation.manager.Manager",
                        return_value=m) as ml:
            with mock.patch("wormhole._dilation.manager.make_side",
                            return_value="us"):
                dil.got_wormhole_versions({"can-dilate": ["1"]})
        # that should create the Manager
        self.assertEqual(ml.mock_calls, [mock.call(send, "us", transit_key,
                                                   None, reactor, eq, coop, no_listen=False)])
        # and tell it to start, and get wait-for-it-to-connect Deferred
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

        # all subsequent DILATE-n messages should get passed to the manager
        self.assertEqual(m.mock_calls, [])
        pleasemsg = dict(type="please", side="them")
        dil.received_dilate(dict_to_bytes(pleasemsg))
        self.assertEqual(m.mock_calls, [mock.call.rx_PLEASE(pleasemsg)])
        clear_mock_calls(m)

        hintmsg = dict(type="connection-hints")
        dil.received_dilate(dict_to_bytes(hintmsg))
        self.assertEqual(m.mock_calls, [mock.call.rx_HINTS(hintmsg)])
        clear_mock_calls(m)

        # we're nominally the LEADER, and the leader would not normally be
        # receiving a RECONNECT, but since we've mocked out the Manager it
        # won't notice
        dil.received_dilate(dict_to_bytes(dict(type="reconnect")))
        self.assertEqual(m.mock_calls, [mock.call.rx_RECONNECT()])
        clear_mock_calls(m)

        dil.received_dilate(dict_to_bytes(dict(type="reconnecting")))
        self.assertEqual(m.mock_calls, [mock.call.rx_RECONNECTING()])
        clear_mock_calls(m)

        dil.received_dilate(dict_to_bytes(dict(type="unknown")))
        self.assertEqual(m.mock_calls, [])
        self.flushLoggedErrors(UnknownDilationMessageType)

    def test_peer_cannot_dilate(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        self.assertNoResult(d1)

        dil._transit_key = b"\x01" * 32
        dil.got_wormhole_versions({})  # missing "can-dilate"
        eq.flush_sync()
        f = self.failureResultOf(d1)
        f.check(OldPeerCannotDilateError)

    def test_disjoint_versions(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        d1 = dil.dilate()
        self.assertNoResult(d1)

        dil._transit_key = b"key"
        dil.got_wormhole_versions({"can-dilate": [-1]})
        eq.flush_sync()
        f = self.failureResultOf(d1)
        f.check(OldPeerCannotDilateError)

    def test_early_dilate_messages(self):
        dil, send, reactor, eq, clock, coop = make_dilator()
        dil._transit_key = b"key"
        d1 = dil.dilate()
        self.assertNoResult(d1)
        pleasemsg = dict(type="please", side="them")
        dil.received_dilate(dict_to_bytes(pleasemsg))
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
                                                   None, reactor, eq, coop, no_listen=False)])
        self.assertEqual(m.mock_calls, [mock.call.start(),
                                        mock.call.rx_PLEASE(pleasemsg),
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
                                                   relay, reactor, eq, coop, no_listen=False),
                                         mock.call().start(),
                                         mock.call().when_first_connected()])


LEADER = "ff3456abcdef"
FOLLOWER = "123456abcdef"


def make_manager(leader=True):
    class Holder:
        pass
    h = Holder()
    h.send = mock.Mock()
    alsoProvides(h.send, ISend)
    if leader:
        side = LEADER
    else:
        side = FOLLOWER
    h.key = b"\x00" * 32
    h.relay = None
    h.reactor = object()
    h.clock = Clock()
    h.eq = EventualQueue(h.clock)
    term = mock.Mock(side_effect=lambda: True)  # one write per Eventual tick

    def term_factory():
        return term
    h.coop = Cooperator(terminationPredicateFactory=term_factory,
                        scheduler=h.eq.eventually)
    h.inbound = mock.Mock()
    h.Inbound = mock.Mock(return_value=h.inbound)
    h.outbound = mock.Mock()
    h.Outbound = mock.Mock(return_value=h.outbound)
    h.hostaddr = object()
    with mock.patch("wormhole._dilation.manager.Inbound", h.Inbound):
        with mock.patch("wormhole._dilation.manager.Outbound", h.Outbound):
            with mock.patch("wormhole._dilation.manager._WormholeAddress",
                            return_value=h.hostaddr):
                m = Manager(h.send, side, h.key, h.relay, h.reactor, h.eq, h.coop)
    return m, h


class TestManager(unittest.TestCase):
    def test_make_side(self):
        side = make_side()
        self.assertEqual(type(side), type(u""))
        self.assertEqual(len(side), 2 * 6)

    def test_create(self):
        m, h = make_manager()

    def test_leader(self):
        m, h = make_manager(leader=True)
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(h.Inbound.mock_calls, [mock.call(m, h.hostaddr)])
        self.assertEqual(h.Outbound.mock_calls, [mock.call(m, h.coop)])

        m.start()
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-0",
                           dict_to_bytes({"type": "please", "side": LEADER}))
            ])
        clear_mock_calls(h.send)

        wfc_d = m.when_first_connected()
        self.assertNoResult(wfc_d)

        # ignore early hints
        m.rx_HINTS({})
        self.assertEqual(h.send.mock_calls, [])

        c = mock.Mock()
        connector = mock.Mock(return_value=c)
        with mock.patch("wormhole._dilation.manager.Connector", connector):
            # receiving this PLEASE triggers creation of the Connector
            m.rx_PLEASE({"side": FOLLOWER})
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(connector.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      LEADER, roles.LEADER),
            ])
        self.assertEqual(c.mock_calls, [mock.call.start()])
        clear_mock_calls(connector, c)

        self.assertNoResult(wfc_d)

        # now any inbound hints should get passed to our Connector
        with mock.patch("wormhole._dilation.manager.parse_hint",
                        side_effect=["p1", None, "p3"]) as ph:
            m.rx_HINTS({"hints": [1, 2, 3]})
        self.assertEqual(ph.mock_calls, [mock.call(1), mock.call(2), mock.call(3)])
        self.assertEqual(c.mock_calls, [mock.call.got_hints(["p1", "p3"])])
        clear_mock_calls(ph, c)

        # and we send out any (listening) hints from our Connector
        m.send_hints([1, 2])
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-1",
                           dict_to_bytes({"type": "connection-hints",
                                          "hints": [1, 2]}))
            ])
        clear_mock_calls(h.send)

        # the first successful connection fires when_first_connected(), so
        # the Dilator can create and return the endpoints
        c1 = mock.Mock()
        m.connector_connection_made(c1)

        self.assertEqual(h.inbound.mock_calls, [mock.call.use_connection(c1)])
        self.assertEqual(h.outbound.mock_calls, [mock.call.use_connection(c1)])
        clear_mock_calls(h.inbound, h.outbound)

        h.eq.flush_sync()
        self.successResultOf(wfc_d)  # fires with None
        wfc_d2 = m.when_first_connected()
        h.eq.flush_sync()
        self.successResultOf(wfc_d2)

        scid0 = b"\x00\x00\x00\x00"
        sc0 = mock.Mock()
        m.set_subchannel_zero(scid0, sc0)
        listen_ep = mock.Mock()
        m.set_listener_endpoint(listen_ep)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.set_subchannel_zero(scid0, sc0),
            mock.call.set_listener_endpoint(listen_ep),
            ])
        clear_mock_calls(h.inbound)

        # the Leader making a new outbound channel should get scid=1
        scid1 = to_be4(1)
        self.assertEqual(m.allocate_subchannel_id(), scid1)
        r1 = Open(10, scid1)  # seqnum=10
        h.outbound.build_record = mock.Mock(return_value=r1)
        m.send_open(scid1)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.build_record(Open, scid1),
            mock.call.queue_and_send_record(r1),
            ])
        clear_mock_calls(h.outbound)

        r2 = Data(11, scid1, b"data")
        h.outbound.build_record = mock.Mock(return_value=r2)
        m.send_data(scid1, b"data")
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.build_record(Data, scid1, b"data"),
            mock.call.queue_and_send_record(r2),
            ])
        clear_mock_calls(h.outbound)

        r3 = Close(12, scid1)
        h.outbound.build_record = mock.Mock(return_value=r3)
        m.send_close(scid1)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.build_record(Close, scid1),
            mock.call.queue_and_send_record(r3),
            ])
        clear_mock_calls(h.outbound)

        # ack the OPEN
        m.got_record(Ack(10))
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.handle_ack(10)
            ])
        clear_mock_calls(h.outbound)

        # test that inbound records get acked and routed to Inbound
        h.inbound.is_record_old = mock.Mock(return_value=False)
        scid2 = to_be4(2)
        o200 = Open(200, scid2)
        m.got_record(o200)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.send_if_connected(Ack(200))
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.is_record_old(o200),
            mock.call.update_ack_watermark(200),
            mock.call.handle_open(scid2),
            ])
        clear_mock_calls(h.outbound, h.inbound)

        # old (duplicate) records should provoke new Acks, but not get
        # forwarded
        h.inbound.is_record_old = mock.Mock(return_value=True)
        m.got_record(o200)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.send_if_connected(Ack(200))
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.is_record_old(o200),
            ])
        clear_mock_calls(h.outbound, h.inbound)

        # check Data and Close too
        h.inbound.is_record_old = mock.Mock(return_value=False)
        d201 = Data(201, scid2, b"data")
        m.got_record(d201)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.send_if_connected(Ack(201))
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.is_record_old(d201),
            mock.call.update_ack_watermark(201),
            mock.call.handle_data(scid2, b"data"),
            ])
        clear_mock_calls(h.outbound, h.inbound)

        c202 = Close(202, scid2)
        m.got_record(c202)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.send_if_connected(Ack(202))
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.is_record_old(c202),
            mock.call.update_ack_watermark(202),
            mock.call.handle_close(scid2),
            ])
        clear_mock_calls(h.outbound, h.inbound)

        # Now we lose the connection. The Leader should tell the other side
        # that we're reconnecting.

        m.connector_connection_lost()
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-2",
                           dict_to_bytes({"type": "reconnect"}))
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.stop_using_connection()
            ])
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.stop_using_connection()
            ])
        clear_mock_calls(h.send, h.inbound, h.outbound)

        # leader does nothing (stays in FLUSHING) until the follower acks by
        # sending RECONNECTING

        # inbound hints should be ignored during FLUSHING
        with mock.patch("wormhole._dilation.manager.parse_hint",
                        return_value=None) as ph:
            m.rx_HINTS({"hints": [1, 2, 3]})
        self.assertEqual(ph.mock_calls, [])  # ignored

        c2 = mock.Mock()
        connector2 = mock.Mock(return_value=c2)
        with mock.patch("wormhole._dilation.manager.Connector", connector2):
            # this triggers creation of a new Connector
            m.rx_RECONNECTING()
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(connector2.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      LEADER, roles.LEADER),
            ])
        self.assertEqual(c2.mock_calls, [mock.call.start()])
        clear_mock_calls(connector2, c2)

        self.assertEqual(h.inbound.mock_calls, [])
        self.assertEqual(h.outbound.mock_calls, [])

        # and a new connection should re-register with Inbound/Outbound,
        # which are responsible for re-sending unacked queued messages
        c3 = mock.Mock()
        m.connector_connection_made(c3)

        self.assertEqual(h.inbound.mock_calls, [mock.call.use_connection(c3)])
        self.assertEqual(h.outbound.mock_calls, [mock.call.use_connection(c3)])
        clear_mock_calls(h.inbound, h.outbound)

    def test_follower(self):
        m, h = make_manager(leader=False)

        m.start()
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-0",
                           dict_to_bytes({"type": "please", "side": FOLLOWER}))
            ])
        clear_mock_calls(h.send)

        c = mock.Mock()
        connector = mock.Mock(return_value=c)
        with mock.patch("wormhole._dilation.manager.Connector", connector):
            # receiving this PLEASE triggers creation of the Connector
            m.rx_PLEASE({"side": LEADER})
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(connector.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      FOLLOWER, roles.FOLLOWER),
            ])
        self.assertEqual(c.mock_calls, [mock.call.start()])
        clear_mock_calls(connector, c)

        # get connected, then lose the connection
        c1 = mock.Mock()
        m.connector_connection_made(c1)
        self.assertEqual(h.inbound.mock_calls, [mock.call.use_connection(c1)])
        self.assertEqual(h.outbound.mock_calls, [mock.call.use_connection(c1)])
        clear_mock_calls(h.inbound, h.outbound)

        # now lose the connection. As the follower, we don't notify the
        # leader, we just wait for them to notice
        m.connector_connection_lost()
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.stop_using_connection()
            ])
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.stop_using_connection()
            ])
        clear_mock_calls(h.send, h.inbound, h.outbound)

        # now we get a RECONNECT: we should send RECONNECTING
        c2 = mock.Mock()
        connector2 = mock.Mock(return_value=c2)
        with mock.patch("wormhole._dilation.manager.Connector", connector2):
            m.rx_RECONNECT()
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-1",
                           dict_to_bytes({"type": "reconnecting"}))
            ])
        self.assertEqual(connector2.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      FOLLOWER, roles.FOLLOWER),
            ])
        self.assertEqual(c2.mock_calls, [mock.call.start()])
        clear_mock_calls(connector2, c2)

        # while we're trying to connect, we get told to stop again, so we
        # should abandon the connection attempt and start another
        c3 = mock.Mock()
        connector3 = mock.Mock(return_value=c3)
        with mock.patch("wormhole._dilation.manager.Connector", connector3):
            m.rx_RECONNECT()
        self.assertEqual(c2.mock_calls, [mock.call.stop()])
        self.assertEqual(connector3.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      FOLLOWER, roles.FOLLOWER),
            ])
        self.assertEqual(c3.mock_calls, [mock.call.start()])
        clear_mock_calls(c2, connector3, c3)

        m.connector_connection_made(c3)
        # finally if we're already connected, rx_RECONNECT means we should
        # abandon this connection (even though it still looks ok to us), then
        # when the attempt is finished stopping, we should start another

        m.rx_RECONNECT()

        c4 = mock.Mock()
        connector4 = mock.Mock(return_value=c4)
        with mock.patch("wormhole._dilation.manager.Connector", connector4):
            m.connector_connection_lost()
        self.assertEqual(c3.mock_calls, [mock.call.disconnect()])
        self.assertEqual(connector4.mock_calls, [
            mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                      False,  # no_listen
                      None,  # tor
                      None,  # timing
                      FOLLOWER, roles.FOLLOWER),
            ])
        self.assertEqual(c4.mock_calls, [mock.call.start()])
        clear_mock_calls(c3, connector4, c4)

    def test_mirror(self):
        # receive a PLEASE with the same side as us: shouldn't happen
        m, h = make_manager(leader=True)

        m.start()
        clear_mock_calls(h.send)
        e = self.assertRaises(ValueError, m.rx_PLEASE, {"side": LEADER})
        self.assertEqual(str(e), "their side shouldn't be equal: reflection?")

    def test_ping_pong(self):
        m, h = make_manager(leader=False)

        m.got_record(KCM())
        self.flushLoggedErrors(UnexpectedKCM)

        m.got_record(Ping(1))
        self.assertEqual(h.outbound.mock_calls,
                         [mock.call.send_if_connected(Pong(1))])
        clear_mock_calls(h.outbound)

        m.got_record(Pong(2))
        # currently ignored, will eventually update a timer

        m.got_record("not recognized")
        e = self.flushLoggedErrors(UnknownMessageType)
        self.assertEqual(len(e), 1)
        self.assertEqual(str(e[0].value), "not recognized")

        m.send_ping(3)
        self.assertEqual(h.outbound.mock_calls,
                         [mock.call.send_if_connected(Pong(3))])
        clear_mock_calls(h.outbound)

    def test_subchannel(self):
        m, h = make_manager(leader=True)
        sc = object()

        m.subchannel_pauseProducing(sc)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.subchannel_pauseProducing(sc)])
        clear_mock_calls(h.inbound)

        m.subchannel_resumeProducing(sc)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.subchannel_resumeProducing(sc)])
        clear_mock_calls(h.inbound)

        m.subchannel_stopProducing(sc)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.subchannel_stopProducing(sc)])
        clear_mock_calls(h.inbound)

        p = object()
        streaming = object()

        m.subchannel_registerProducer(sc, p, streaming)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.subchannel_registerProducer(sc, p, streaming)])
        clear_mock_calls(h.outbound)

        m.subchannel_unregisterProducer(sc)
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.subchannel_unregisterProducer(sc)])
        clear_mock_calls(h.outbound)

        m.subchannel_closed("scid", sc)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.subchannel_closed("scid", sc)])
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.subchannel_closed("scid", sc)])
        clear_mock_calls(h.inbound, h.outbound)
