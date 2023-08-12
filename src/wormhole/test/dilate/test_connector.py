from __future__ import print_function, unicode_literals

from unittest import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.task import Clock
from twisted.internet.defer import Deferred
from twisted.internet.address import IPv4Address, IPv6Address, HostnameAddress
from ...eventual import EventualQueue
from ..._interfaces import IDilationManager, IDilationConnector
from ..._hints import DirectTCPV1Hint, RelayV1Hint, TorTCPV1Hint
from ..._dilation import roles
from ..._dilation._noise import NoiseConnection
from ..._dilation.connection import KCM
from ..._dilation.connector import (Connector,
                                    build_sided_relay_handshake,
                                    build_noise,
                                    describe_inbound,
                                    OutboundConnectionFactory,
                                    InboundConnectionFactory,
                                    PROLOGUE_LEADER, PROLOGUE_FOLLOWER,
                                    )
from .common import clear_mock_calls


class Handshake(unittest.TestCase):
    def test_build(self):
        key = b"k"*32
        side = "12345678abcdabcd"
        self.assertEqual(
            build_sided_relay_handshake(key, side),
            b"please relay 3f4147851dbd2589d25b654ee9fb35ed0d3e5f19c5c5403e8e6a195c70f0577a"
            b" for side 12345678abcdabcd\n"
        )


class Outbound(unittest.TestCase):
    def test_no_relay(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        relay_handshake = None
        f = OutboundConnectionFactory(c, relay_handshake, "desc")
        addr = object()
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr, "desc")])
        self.assertEqual(p.mock_calls, [])
        self.assertIdentical(p.factory, f)

    def test_with_relay(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        relay_handshake = b"relay handshake"
        f = OutboundConnectionFactory(c, relay_handshake, "desc")
        addr = object()
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr, "desc")])
        self.assertEqual(p.mock_calls, [mock.call.use_relay(relay_handshake)])
        self.assertIdentical(p.factory, f)


class Inbound(unittest.TestCase):
    def test_build(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        f = InboundConnectionFactory(c)
        addr = IPv4Address("TCP", "1.2.3.4", 55)
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr, "<-tcp:1.2.3.4:55")])
        self.assertIdentical(p.factory, f)


def make_connector(listen=True, tor=False, relay=None, role=roles.LEADER):
    class Holder:
        pass
    h = Holder()
    h.dilation_key = b"key"
    h.relay = relay
    h.manager = mock.Mock()
    alsoProvides(h.manager, IDilationManager)
    h.clock = Clock()
    h.reactor = h.clock
    h.eq = EventualQueue(h.clock)
    h.tor = None
    if tor:
        h.tor = mock.Mock()
    timing = None
    h.side = u"abcd1234abcd5678"
    h.role = role
    c = Connector(h.dilation_key, h.relay, h.manager, h.reactor, h.eq,
                  not listen, h.tor, timing, h.side, h.role)
    return c, h


class TestConnector(unittest.TestCase):
    def test_build(self):
        c, h = make_connector()
        c, h = make_connector(relay="tcp:host:1234")

    def test_connection_abilities(self):
        self.assertEqual(Connector.get_connection_abilities(),
                         [{"type": "direct-tcp-v1"},
                          {"type": "relay-v1"},
                          ])

    def test_build_noise(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")
        build_noise()

    def test_build_protocol_leader(self):
        c, h = make_connector(role=roles.LEADER)
        n0 = mock.Mock()
        p0 = mock.Mock()
        addr = object()
        with mock.patch("wormhole._dilation.connector.build_noise",
                        return_value=n0) as bn:
            with mock.patch("wormhole._dilation.connector.DilatedConnectionProtocol",
                            return_value=p0) as dcp:
                p = c.build_protocol(addr, "desc")
        self.assertEqual(bn.mock_calls, [mock.call()])
        self.assertEqual(n0.mock_calls, [mock.call.set_psks(h.dilation_key),
                                         mock.call.set_as_initiator()])
        self.assertIdentical(p, p0)
        self.assertEqual(dcp.mock_calls,
                         [mock.call(h.eq, h.role, "desc", c, n0,
                                    PROLOGUE_LEADER, PROLOGUE_FOLLOWER)])

    def test_build_protocol_follower(self):
        c, h = make_connector(role=roles.FOLLOWER)
        n0 = mock.Mock()
        p0 = mock.Mock()
        addr = object()
        with mock.patch("wormhole._dilation.connector.build_noise",
                        return_value=n0) as bn:
            with mock.patch("wormhole._dilation.connector.DilatedConnectionProtocol",
                            return_value=p0) as dcp:
                p = c.build_protocol(addr, "desc")
        self.assertEqual(bn.mock_calls, [mock.call()])
        self.assertEqual(n0.mock_calls, [mock.call.set_psks(h.dilation_key),
                                         mock.call.set_as_responder()])
        self.assertIdentical(p, p0)
        self.assertEqual(dcp.mock_calls,
                         [mock.call(h.eq, h.role, "desc", c, n0,
                                    PROLOGUE_FOLLOWER, PROLOGUE_LEADER)])

    def test_start_stop(self):
        c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
        c.start()
        # no relays, so it publishes no hints
        self.assertEqual(h.manager.mock_calls, [])
        # and no listener, so nothing happens until we provide a hint
        c.stop()
        # we stop while we're connecting, so no connections must be stopped

    def test_empty(self):
        c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()
        # no relays, so it publishes no hints
        self.assertEqual(h.manager.mock_calls, [])
        # and no listener, so nothing happens until we provide a hint
        self.assertEqual(c._schedule_connection.mock_calls, [])
        c.stop()

    def test_basic(self):
        c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()
        # no relays, so it publishes no hints
        self.assertEqual(h.manager.mock_calls, [])
        # and no listener, so nothing happens until we provide a hint
        self.assertEqual(c._schedule_connection.mock_calls, [])

        hint = DirectTCPV1Hint("foo", 55, 0.0)
        c.got_hints([hint])

        # received hints don't get published
        self.assertEqual(h.manager.mock_calls, [])
        # they just schedule a connection
        self.assertEqual(c._schedule_connection.mock_calls,
                         [mock.call(0.0, DirectTCPV1Hint("foo", 55, 0.0),
                                    is_relay=False)])

    def test_listen_addresses(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]):
            self.assertEqual(c._get_listener_addresses(),
                             ["1.2.3.4", "5.6.7.8"])
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1"]):
            # some test hosts, including the appveyor VMs, *only* have
            # 127.0.0.1, and the tests will hang badly if we remove it.
            self.assertEqual(c._get_listener_addresses(), ["127.0.0.1"])

    def test_listen(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        c._start_listener = mock.Mock()
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]):
            c.start()
        self.assertEqual(c._start_listener.mock_calls,
                         [mock.call(["1.2.3.4", "5.6.7.8"])])

    def test_start_listen(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        ep = mock.Mock()
        d = Deferred()
        ep.listen = mock.Mock(return_value=d)
        with mock.patch("wormhole._dilation.connector.serverFromString",
                        return_value=ep) as sfs:
            c._start_listener(["1.2.3.4", "5.6.7.8"])
        self.assertEqual(sfs.mock_calls, [mock.call(h.reactor, "tcp:0")])
        lp = mock.Mock()
        host = mock.Mock()
        host.port = 66
        lp.getHost = mock.Mock(return_value=host)
        d.callback(lp)
        self.assertEqual(h.manager.mock_calls,
                         [mock.call.send_hints([{"type": "direct-tcp-v1",
                                                 "hostname": "1.2.3.4",
                                                 "port": 66,
                                                 "priority": 0.0
                                                 },
                                                {"type": "direct-tcp-v1",
                                                 "hostname": "5.6.7.8",
                                                 "port": 66,
                                                 "priority": 0.0
                                                 },
                                                ])])

    def test_schedule_connection_no_relay(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        hint = DirectTCPV1Hint("foo", 55, 0.0)
        ep = mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep]) as efho:
            c._schedule_connection(0.0, hint, False)
        self.assertEqual(efho.mock_calls, [mock.call(hint, h.tor, h.reactor)])
        self.assertEqual(ep.mock_calls, [])
        d = Deferred()
        ep.connect = mock.Mock(side_effect=[d])
        # direct hints are scheduled for T+0.0
        f = mock.Mock()
        with mock.patch("wormhole._dilation.connector.OutboundConnectionFactory",
                        return_value=f) as ocf:
            h.clock.advance(1.0)
        self.assertEqual(ocf.mock_calls, [mock.call(c, None, "->tcp:foo:55")])
        self.assertEqual(ep.connect.mock_calls, [mock.call(f)])
        p = mock.Mock()
        d.callback(p)
        self.assertEqual(p.mock_calls,
                         [mock.call.when_disconnected(),
                          mock.call.when_disconnected().addCallback(c._pending_connections.discard)])

    def test_schedule_connection_relay(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        hint = DirectTCPV1Hint("foo", 55, 0.0)
        ep = mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep]) as efho:
            c._schedule_connection(0.0, hint, True)
        self.assertEqual(efho.mock_calls, [mock.call(hint, h.tor, h.reactor)])
        self.assertEqual(ep.mock_calls, [])
        d = Deferred()
        ep.connect = mock.Mock(side_effect=[d])
        # direct hints are scheduled for T+0.0
        f = mock.Mock()
        with mock.patch("wormhole._dilation.connector.OutboundConnectionFactory",
                        return_value=f) as ocf:
            h.clock.advance(1.0)
        handshake = build_sided_relay_handshake(h.dilation_key, h.side)
        self.assertEqual(ocf.mock_calls, [mock.call(c, handshake, "->relay:tcp:foo:55")])

    def test_listen_but_tor(self):
        c, h = make_connector(listen=True, tor=True, role=roles.LEADER)
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]) as fa:
            c.start()
        # don't even look up addresses
        self.assertEqual(fa.mock_calls, [])
        # no relays and the listener isn't ready yet, so no hints yet
        self.assertEqual(h.manager.mock_calls, [])

    def test_no_listen(self):
        c, h = make_connector(listen=False, tor=False, role=roles.LEADER)
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]) as fa:
            c.start()
        # don't even look up addresses
        self.assertEqual(fa.mock_calls, [])
        self.assertEqual(h.manager.mock_calls, [])

    def test_relay_delay(self):
        # given a direct connection and a relay, we should see the direct
        # connection initiated at T+0 seconds, and the relay at T+RELAY_DELAY
        c, h = make_connector(listen=True, relay=None, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c._start_listener = mock.Mock()
        c.start()
        hint1 = DirectTCPV1Hint("foo", 55, 0.0)
        hint2 = DirectTCPV1Hint("bar", 55, 0.0)
        hint3 = RelayV1Hint([DirectTCPV1Hint("relay", 55, 0.0)])
        c.got_hints([hint1, hint2, hint3])
        self.assertEqual(c._schedule_connection.mock_calls,
                         [mock.call(0.0, hint1, is_relay=False),
                          mock.call(0.0, hint2, is_relay=False),
                          mock.call(c.RELAY_DELAY, hint3.hints[0], is_relay=True),
                          ])

    def test_initial_relay(self):
        c, h = make_connector(listen=False, relay="tcp:foo:55", role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()
        self.assertEqual(h.manager.mock_calls,
                         [mock.call.send_hints([{"type": "relay-v1",
                                                 "hints": [
                                                     {"type": "direct-tcp-v1",
                                                      "hostname": "foo",
                                                      "port": 55,
                                                      "priority": 0.0
                                                      },
                                                     ],
                                                 }])])
        self.assertEqual(c._schedule_connection.mock_calls,
                         [mock.call(0.0, DirectTCPV1Hint("foo", 55, 0.0),
                                    is_relay=True)])

    def test_tor_no_manager(self):
        # tor hints should be ignored if we don't have a Tor manager to use them
        c, h = make_connector(listen=False, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()
        hint = TorTCPV1Hint("foo", 55, 0.0)
        c.got_hints([hint])
        self.assertEqual(h.manager.mock_calls, [])
        self.assertEqual(c._schedule_connection.mock_calls, [])

    def test_tor_with_manager(self):
        # tor hints should be processed if we do have a Tor manager
        c, h = make_connector(listen=False, tor=True, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()
        hint = TorTCPV1Hint("foo", 55, 0.0)
        c.got_hints([hint])
        self.assertEqual(c._schedule_connection.mock_calls,
                         [mock.call(0.0, hint, is_relay=False)])

    def test_priorities(self):
        # given two hints with different priorities, we should somehow prefer
        # one. This is a placeholder to fill in once we implement priorities.
        pass


class Race(unittest.TestCase):
    def test_one_leader(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        lp = mock.Mock()

        def start_listener(addresses):
            c._listeners.add(lp)
        c._start_listener = start_listener
        c._schedule_connection = mock.Mock()
        c.start()
        self.assertEqual(c._listeners, set([lp]))

        p1 = mock.Mock()  # DilatedConnectionProtocol instance
        c.add_candidate(p1)
        self.assertEqual(h.manager.mock_calls, [])
        h.eq.flush_sync()
        self.assertEqual(h.manager.mock_calls, [mock.call.connector_connection_made(p1)])
        self.assertEqual(p1.mock_calls,
                         [mock.call.select(h.manager),
                          mock.call.send_record(KCM())])
        self.assertEqual(lp.mock_calls[0], mock.call.stopListening())
        # stop_listeners() uses a DeferredList, so we ignore the second call

    def test_one_follower(self):
        c, h = make_connector(listen=True, role=roles.FOLLOWER)
        lp = mock.Mock()

        def start_listener(addresses):
            c._listeners.add(lp)
        c._start_listener = start_listener
        c._schedule_connection = mock.Mock()
        c.start()
        self.assertEqual(c._listeners, set([lp]))

        p1 = mock.Mock()  # DilatedConnectionProtocol instance
        c.add_candidate(p1)
        self.assertEqual(h.manager.mock_calls, [])
        h.eq.flush_sync()
        self.assertEqual(h.manager.mock_calls, [mock.call.connector_connection_made(p1)])
        # just like LEADER, but follower doesn't send KCM now (it sent one
        # earlier, to tell the leader that this connection looks viable)
        self.assertEqual(p1.mock_calls,
                         [mock.call.select(h.manager)])
        self.assertEqual(lp.mock_calls[0], mock.call.stopListening())
        # stop_listeners() uses a DeferredList, so we ignore the second call

    # TODO: make sure a pending connection is abandoned when the listener
    # answers successfully

    # TODO: make sure a second pending connection is abandoned when the first
    # connection succeeds

    def test_late(self):
        c, h = make_connector(listen=False, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()

        p1 = mock.Mock()  # DilatedConnectionProtocol instance
        c.add_candidate(p1)
        self.assertEqual(h.manager.mock_calls, [])
        h.eq.flush_sync()
        self.assertEqual(h.manager.mock_calls, [mock.call.connector_connection_made(p1)])
        clear_mock_calls(h.manager)
        self.assertEqual(p1.mock_calls,
                         [mock.call.select(h.manager),
                          mock.call.send_record(KCM())])

        # late connection is ignored
        p2 = mock.Mock()
        c.add_candidate(p2)
        self.assertEqual(h.manager.mock_calls, [])

    # make sure an established connection is dropped when stop() is called
    def test_stop(self):
        c, h = make_connector(listen=False, role=roles.LEADER)
        c._schedule_connection = mock.Mock()
        c.start()

        p1 = mock.Mock()  # DilatedConnectionProtocol instance
        c.add_candidate(p1)
        self.assertEqual(h.manager.mock_calls, [])
        h.eq.flush_sync()
        self.assertEqual(p1.mock_calls,
                         [mock.call.select(h.manager),
                          mock.call.send_record(KCM())])
        self.assertEqual(h.manager.mock_calls, [mock.call.connector_connection_made(p1)])

        c.stop()


class Describe(unittest.TestCase):
    def test_describe_inbound(self):
        self.assertEqual(describe_inbound(HostnameAddress("example.com", 1234)),
                         "<-tcp:example.com:1234")
        self.assertEqual(describe_inbound(IPv4Address("TCP", "1.2.3.4", 1234)),
                         "<-tcp:1.2.3.4:1234")
        self.assertEqual(describe_inbound(IPv6Address("TCP", "::1", 1234)),
                         "<-tcp:[::1]:1234")
        other = "none-of-the-above"
        self.assertEqual(describe_inbound(other), "<-%r" % other)
