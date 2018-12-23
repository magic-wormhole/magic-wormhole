from __future__ import print_function, unicode_literals

import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.task import Clock
from twisted.internet.defer import Deferred
#from twisted.internet import endpoints
from ...eventual import EventualQueue
from ..._interfaces import IDilationManager, IDilationConnector
from ..._dilation import roles
from ..._hints import DirectTCPV1Hint, RelayV1Hint, TorTCPV1Hint
from ..._dilation.connector import (#describe_hint_obj, parse_hint_argv,
                                   #parse_tcp_v1_hint, parse_hint, encode_hint,
                                   Connector,
    build_sided_relay_handshake,
    build_noise,
    OutboundConnectionFactory,
    InboundConnectionFactory,
    PROLOGUE_LEADER, PROLOGUE_FOLLOWER,
    )

class Handshake(unittest.TestCase):
    def test_build(self):
        key = b"k"*32
        side = "12345678abcdabcd"
        self.assertEqual(build_sided_relay_handshake(key, side),
                         b"please relay 3f4147851dbd2589d25b654ee9fb35ed0d3e5f19c5c5403e8e6a195c70f0577a for side 12345678abcdabcd\n")

class Outbound(unittest.TestCase):
    def test_no_relay(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        relay_handshake = None
        f = OutboundConnectionFactory(c, relay_handshake)
        addr = object()
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr)])
        self.assertEqual(p.mock_calls, [])
        self.assertIdentical(p.factory, f)

    def test_with_relay(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        relay_handshake = b"relay handshake"
        f = OutboundConnectionFactory(c, relay_handshake)
        addr = object()
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr)])
        self.assertEqual(p.mock_calls, [mock.call.use_relay(relay_handshake)])
        self.assertIdentical(p.factory, f)

class Inbound(unittest.TestCase):
    def test_build(self):
        c = mock.Mock()
        alsoProvides(c, IDilationConnector)
        p0 = mock.Mock()
        c.build_protocol = mock.Mock(return_value=p0)
        f = InboundConnectionFactory(c)
        addr = object()
        p = f.buildProtocol(addr)
        self.assertIdentical(p, p0)
        self.assertEqual(c.mock_calls, [mock.call.build_protocol(addr)])
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
                p = c.build_protocol(addr)
        self.assertEqual(bn.mock_calls, [mock.call()])
        self.assertEqual(n0.mock_calls, [mock.call.set_psks(h.dilation_key),
                                         mock.call.set_as_initiator()])
        self.assertIdentical(p, p0)
        self.assertEqual(dcp.mock_calls,
                         [mock.call(h.eq, h.role, c, n0,
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
                p = c.build_protocol(addr)
        self.assertEqual(bn.mock_calls, [mock.call()])
        self.assertEqual(n0.mock_calls, [mock.call.set_psks(h.dilation_key),
                                         mock.call.set_as_responder()])
        self.assertIdentical(p, p0)
        self.assertEqual(dcp.mock_calls,
                         [mock.call(h.eq, h.role, c, n0,
                                    PROLOGUE_FOLLOWER, PROLOGUE_LEADER)])

    def test_start_stop(self):
        c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
        c.start()
        # no relays, so it publishes no hints
        self.assertEqual(h.manager.mock_calls, [])
        # and no listener, so nothing happens until we provide a hint
        c.stop()
        # we stop while we're connecting, so no connections must be stopped

    def test_basic(self):
        c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
        c.start()
        # no relays, so it publishes no hints
        self.assertEqual(h.manager.mock_calls, [])
        # and no listener, so nothing happens until we provide a hint

        ep0 = mock.Mock()
        ep0_connect_d = Deferred()
        ep0.connect = mock.Mock(return_value=ep0_connect_d)
        efho = mock.Mock(side_effect=[ep0])
        hint0 = DirectTCPV1Hint("foo", 55, 0.0)
        dho = mock.Mock(side_effect=["desc0"])
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        efho):
            with mock.patch("wormhole._dilation.connector.describe_hint_obj", dho):
                c.got_hints([hint0])
        self.assertEqual(efho.mock_calls, [mock.call(hint0, h.tor, h.reactor)])
        self.assertEqual(dho.mock_calls, [mock.call(hint0, False, h.tor)])
        f0 = mock.Mock()
        with mock.patch("wormhole._dilation.connector.OutboundConnectionFactory",
                        return_value=f0) as ocf:
            h.clock.advance(c.RELAY_DELAY / 2 + 0.01)
        self.assertEqual(ocf.mock_calls, [mock.call(c, None)])
        self.assertEqual(ep0.connect.mock_calls, [mock.call(f0)])

        p = mock.Mock()
        ep0_connect_d.callback(p)
        self.assertEqual(p.mock_calls,
                         [mock.call.when_disconnected(),
                          mock.call.when_disconnected().addCallback(c._pending_connections.discard)])

    def test_listen(self):
        c, h = make_connector(listen=True, role=roles.LEADER)
        d = Deferred()
        ep = mock.Mock()
        ep.listen = mock.Mock(return_value=d)
        f = mock.Mock()
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]):
            with mock.patch("wormhole._dilation.connector.serverFromString",
                            side_effect=[ep]):
                with mock.patch("wormhole._dilation.connector.InboundConnectionFactory",
                                return_value=f):
                    c.start()
        # no relays and the listener isn't ready yet, so no hints yet
        self.assertEqual(h.manager.mock_calls, [])
        # but a listener was started
        self.assertEqual(ep.mock_calls, [mock.call.listen(f)])
        lp = mock.Mock()
        host = mock.Mock()
        host.port = 2345
        lp.getHost = mock.Mock(return_value=host)
        d.callback(lp)
        self.assertEqual(h.manager.mock_calls,
                         [mock.call.send_hints(
                             [{"type": "direct-tcp-v1", "hostname": "1.2.3.4",
                               "port": 2345, "priority": 0.0},
                              {"type": "direct-tcp-v1", "hostname": "5.6.7.8",
                               "port": 2345, "priority": 0.0},
                              ])])

    def test_listen_but_tor(self):
        c, h = make_connector(listen=True, tor=True, role=roles.LEADER)
        with mock.patch("wormhole.ipaddrs.find_addresses",
                        return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]) as fa:
            c.start()
        # don't even look up addresses
        self.assertEqual(fa.mock_calls, [])
        # no relays and the listener isn't ready yet, so no hints yet
        self.assertEqual(h.manager.mock_calls, [])

    def test_listen_only_loopback(self):
        # some test hosts, including the appveyor VMs, *only* have
        # 127.0.0.1, and the tests will hang badly if we remove it.
        c, h = make_connector(listen=True, role=roles.LEADER)
        d = Deferred()
        ep = mock.Mock()
        ep.listen = mock.Mock(return_value=d)
        f = mock.Mock()
        with mock.patch("wormhole.ipaddrs.find_addresses", return_value=["127.0.0.1"]):
            with mock.patch("wormhole._dilation.connector.serverFromString",
                            side_effect=[ep]):
                with mock.patch("wormhole._dilation.connector.InboundConnectionFactory",
                                return_value=f):
                    c.start()
        # no relays and the listener isn't ready yet, so no hints yet
        self.assertEqual(h.manager.mock_calls, [])
        # but a listener was started
        self.assertEqual(ep.mock_calls, [mock.call.listen(f)])
        lp = mock.Mock()
        host = mock.Mock()
        host.port = 2345
        lp.getHost = mock.Mock(return_value=host)
        d.callback(lp)
        self.assertEqual(h.manager.mock_calls,
                         [mock.call.send_hints(
                             [{"type": "direct-tcp-v1", "hostname": "127.0.0.1",
                               "port": 2345, "priority": 0.0},
                              ])])

    def OFFtest_relay_delay(self):
        # given a direct connection and a relay, we should see the direct
        # connection initiated at T+0 seconds, and the relay at T+RELAY_DELAY
        c, h = make_connector(listen=False, relay="tcp:foo:55", role=roles.LEADER)
        c.start()
        hint1 = DirectTCPV1Hint("foo", 55, 0.0)
        hint2 = DirectTCPV1Hint("bar", 55, 0.0)
        hint3 = RelayV1Hint([DirectTCPV1Hint("relay", 55, 0.0)])
        ep1, ep2, ep3 = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep1, ep2, ep3]):
            c.got_hints([hint1, hint2, hint3])
        self.assertEqual(ep1.mock_calls, [])
        self.assertEqual(ep2.mock_calls, [])
        self.assertEqual(ep3.mock_calls, [])

        h.clock.advance(c.RELAY_DELAY / 2 + 0.01)
        self.assertEqual(len(ep1.mock_calls), 2)
        self.assertEqual(len(ep2.mock_calls), 2)
        self.assertEqual(ep3.mock_calls, [])

        h.clock.advance(c.RELAY_DELAY)
        self.assertEqual(len(ep1.mock_calls), 2)
        self.assertEqual(len(ep2.mock_calls), 2)
        self.assertEqual(len(ep3.mock_calls), 2)

    def test_initial_relay(self):
        c, h = make_connector(listen=False, relay="tcp:foo:55", role=roles.LEADER)
        ep = mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep]) as efho:
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
        self.assertEqual(len(efho.mock_calls), 1)

    def test_tor_no_manager(self):
        # tor hints should be ignored if we don't have a Tor manager to use them
        c, h = make_connector(listen=False, role=roles.LEADER)
        c.start()
        hint = TorTCPV1Hint("foo", 55, 0.0)
        ep = mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep]):
            c.got_hints([hint])
        self.assertEqual(ep.mock_calls, [])

        h.clock.advance(c.RELAY_DELAY * 2)
        self.assertEqual(ep.mock_calls, [])

    def test_tor_with_manager(self):
        # tor hints should be processed if we do have a Tor manager
        c, h = make_connector(listen=False, tor=True, role=roles.LEADER)
        c.start()
        hint = TorTCPV1Hint("foo", 55, 0.0)
        ep = mock.Mock()
        with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                        side_effect=[ep]):
            c.got_hints([hint])
        self.assertEqual(ep.mock_calls, [])

        h.clock.advance(c.RELAY_DELAY * 2)
        self.assertEqual(len(ep.mock_calls), 2)


    def test_priorities(self):
        # given two hints with different priorities, we should somehow prefer
        # one. This is a placeholder to fill in once we implement priorities.
        pass
