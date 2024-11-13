from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.task import Clock, Cooperator
from twisted.internet.interfaces import IStreamServerEndpoint
from unittest import mock
from ...eventual import EventualQueue
from ..._interfaces import ISend, ITerminator, ISubChannel
from ...util import dict_to_bytes
from ..._dilation import roles
from ..._dilation.manager import (Dilator, Manager, make_side,
                                  OldPeerCannotDilateError,
                                  UnknownDilationMessageType,
                                  UnexpectedKCM,
                                  UnknownMessageType)
from ..._dilation.connection import Open, Data, Close, Ack, KCM, Ping, Pong
from ..._dilation.subchannel import _SubchannelAddress
from .common import clear_mock_calls


class Holder():
    pass


def make_dilator():
    h = Holder()
    h.reactor = object()
    h.clock = Clock()
    h.eq = EventualQueue(h.clock)
    term = mock.Mock(side_effect=lambda: True)  # one write per Eventual tick

    def term_factory():
        return term
    h.coop = Cooperator(terminationPredicateFactory=term_factory,
                        scheduler=h.eq.eventually)
    h.send = mock.Mock()
    alsoProvides(h.send, ISend)
    dil = Dilator(h.reactor, h.eq, h.coop)
    h.terminator = mock.Mock()
    alsoProvides(h.terminator, ITerminator)
    dil.wire(h.send, h.terminator)
    return dil, h


class TestDilator(unittest.TestCase):
    # we should test the interleavings between:
    # * application calls w.dilate() and gets back endpoints
    # * wormhole gets: dilation key, VERSION, 0-n dilation messages

    def test_dilate_first(self):
        (dil, h) = make_dilator()
        side = object()
        m = mock.Mock()
        eps = object()
        m.get_endpoints = mock.Mock(return_value=eps)
        mm = mock.Mock(side_effect=[m])
        with mock.patch("wormhole._dilation.manager.Manager", mm), \
             mock.patch("wormhole._dilation.manager.make_side",
                        return_value=side):
            eps1 = dil.dilate()
            eps2 = dil.dilate()
        self.assertIdentical(eps1, eps)
        self.assertIdentical(eps1, eps2)
        self.assertEqual(mm.mock_calls, [mock.call(h.send, side, None,
                                                   h.reactor, h.eq, h.coop, False)])

        self.assertEqual(m.mock_calls, [mock.call.get_endpoints(),
                                        mock.call.get_endpoints()])
        clear_mock_calls(m)

        key = b"key"
        transit_key = object()
        with mock.patch("wormhole._dilation.manager.derive_key",
                        return_value=transit_key) as dk:
            dil.got_key(key)
        self.assertEqual(dk.mock_calls, [mock.call(key, b"dilation-v1", 32)])
        self.assertEqual(m.mock_calls, [mock.call.got_dilation_key(transit_key)])
        clear_mock_calls(m)

        wv = object()
        dil.got_wormhole_versions(wv)
        self.assertEqual(m.mock_calls, [mock.call.got_wormhole_versions(wv)])
        clear_mock_calls(m)

        dm1 = object()
        dm2 = object()
        dil.received_dilate(dm1)
        dil.received_dilate(dm2)
        self.assertEqual(m.mock_calls, [mock.call.received_dilation_message(dm1),
                                        mock.call.received_dilation_message(dm2),
                                        ])
        clear_mock_calls(m)

        stopped_d = mock.Mock()
        m.when_stopped = mock.Mock(return_value=stopped_d)
        dil.stop()
        self.assertEqual(m.mock_calls, [mock.call.stop(),
                                        mock.call.when_stopped(),
                                        ])

    def test_dilate_later(self):
        (dil, h) = make_dilator()
        m = mock.Mock()
        mm = mock.Mock(side_effect=[m])

        key = b"key"
        transit_key = object()
        with mock.patch("wormhole._dilation.manager.derive_key",
                        return_value=transit_key) as dk:
            dil.got_key(key)
        self.assertEqual(dk.mock_calls, [mock.call(key, b"dilation-v1", 32)])

        wv = object()
        dil.got_wormhole_versions(wv)

        dm1 = object()
        dil.received_dilate(dm1)

        self.assertEqual(mm.mock_calls, [])

        with mock.patch("wormhole._dilation.manager.Manager", mm):
            dil.dilate()
        self.assertEqual(m.mock_calls, [mock.call.got_dilation_key(transit_key),
                                        mock.call.got_wormhole_versions(wv),
                                        mock.call.received_dilation_message(dm1),
                                        mock.call.get_endpoints(),
                                        ])
        clear_mock_calls(m)

        dm2 = object()
        dil.received_dilate(dm2)
        self.assertEqual(m.mock_calls, [mock.call.received_dilation_message(dm2),
                                        ])

    def test_stop_early(self):
        (dil, h) = make_dilator()
        # we stop before w.dilate(), so there is no Manager to stop
        dil.stop()
        self.assertEqual(h.terminator.mock_calls, [mock.call.stoppedD()])

    def test_peer_cannot_dilate(self):
        (dil, h) = make_dilator()
        eps = dil.dilate()

        dil.got_key(b"\x01" * 32)
        dil.got_wormhole_versions({})  # missing "can-dilate"
        d = eps.connect.connect(None)
        h.eq.flush_sync()
        self.failureResultOf(d).check(OldPeerCannotDilateError)

    def test_disjoint_versions(self):
        (dil, h) = make_dilator()
        eps = dil.dilate()

        dil.got_key(b"\x01" * 32)
        dil.got_wormhole_versions({"can-dilate": ["-1"]})
        d = eps.connect.connect(None)
        h.eq.flush_sync()
        self.failureResultOf(d).check(OldPeerCannotDilateError)

    def test_transit_relay(self):
        (dil, h) = make_dilator()
        transit_relay_location = object()
        side = object()
        m = mock.Mock()
        mm = mock.Mock(side_effect=[m])
        with mock.patch("wormhole._dilation.manager.Manager", mm), \
             mock.patch("wormhole._dilation.manager.make_side",
                        return_value=side):
            dil.dilate(transit_relay_location)
        self.assertEqual(mm.mock_calls, [mock.call(h.send, side, transit_relay_location,
                                                   h.reactor, h.eq, h.coop, False)])


LEADER = "ff3456abcdef"
FOLLOWER = "123456abcdef"


def make_manager(leader=True):
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
    h.sc0 = mock.Mock()
    alsoProvides(h.sc0, ISubChannel)
    h.SubChannel = mock.Mock(return_value=h.sc0)
    h.listen_ep = mock.Mock()
    alsoProvides(h.listen_ep, IStreamServerEndpoint)
    with mock.patch("wormhole._dilation.manager.Inbound", h.Inbound), \
         mock.patch("wormhole._dilation.manager.Outbound", h.Outbound), \
         mock.patch("wormhole._dilation.manager.SubChannel", h.SubChannel), \
         mock.patch("wormhole._dilation.manager.SubchannelListenerEndpoint",
                    return_value=h.listen_ep):
        m = Manager(h.send, side, h.relay, h.reactor, h.eq, h.coop)
    h.hostaddr = m._host_addr
    m.got_dilation_key(h.key)
    return m, h


class TestManager(unittest.TestCase):
    def test_make_side(self):
        side = make_side()
        self.assertEqual(type(side), type(u""))
        self.assertEqual(len(side), 2 * 8)

    def test_create(self):
        m, h = make_manager()

    def test_leader(self):
        m, h = make_manager(leader=True)
        self.assertEqual(h.send.mock_calls, [])
        self.assertEqual(h.Inbound.mock_calls, [mock.call(m, h.hostaddr)])
        self.assertEqual(h.Outbound.mock_calls, [mock.call(m, h.coop)])
        scid0 = 0
        sc0_peer_addr = _SubchannelAddress(scid0)
        self.assertEqual(h.SubChannel.mock_calls, [
            mock.call(scid0, m, m._host_addr, sc0_peer_addr),
            ])
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.set_subchannel_zero(scid0, h.sc0),
            mock.call.set_listener_endpoint(h.listen_ep)
            ])
        clear_mock_calls(h.inbound)

        eps = m.get_endpoints()
        self.assertTrue(hasattr(eps, "control"))
        self.assertTrue(hasattr(eps, "connect"))
        self.assertEqual(eps.listen, h.listen_ep)

        m.got_wormhole_versions({"can-dilate": ["1"]})
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-0",
                           dict_to_bytes({"type": "please", "side": LEADER, "use-version": "1"}))
            ])
        clear_mock_calls(h.send)

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
        # the endpoints can activate
        c1 = mock.Mock()
        m.connector_connection_made(c1)

        self.assertEqual(h.inbound.mock_calls, [mock.call.use_connection(c1)])
        self.assertEqual(h.outbound.mock_calls, [mock.call.use_connection(c1)])
        clear_mock_calls(h.inbound, h.outbound)

        # the Leader making a new outbound channel should get scid=1
        scid1 = 1
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
        scid2 = 2
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

        m.got_wormhole_versions({"can-dilate": ["1"]})
        self.assertEqual(h.send.mock_calls, [
            mock.call.send("dilate-0",
                           dict_to_bytes({"type": "please", "side": FOLLOWER, "use-version": "1"}))
            ])
        clear_mock_calls(h.send)
        clear_mock_calls(h.inbound)

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
        clear_mock_calls(h.inbound)
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

        m.subchannel_closed(4, sc)
        self.assertEqual(h.inbound.mock_calls, [
            mock.call.subchannel_closed(4, sc)])
        self.assertEqual(h.outbound.mock_calls, [
            mock.call.subchannel_closed(4, sc)])
        clear_mock_calls(h.inbound, h.outbound)

    def test_unknown_message(self):
        # receive a PLEASE with the same side as us: shouldn't happen
        m, h = make_manager(leader=True)
        m.start()

        m.received_dilation_message(dict_to_bytes(dict(type="unknown")))
        self.flushLoggedErrors(UnknownDilationMessageType)

    # TODO: test transit relay is used
