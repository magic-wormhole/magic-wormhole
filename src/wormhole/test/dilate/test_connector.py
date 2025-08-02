from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.task import Clock
from twisted.internet.defer import Deferred
from twisted.internet.address import IPv4Address, IPv6Address, HostnameAddress
import pytest

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
from ..._status import DilationHint
from .common import clear_mock_calls


def test_build():
    key = b"k"*32
    side = "12345678abcdabcd"
    assert build_sided_relay_handshake(key, side) == \
        b"please relay 3f4147851dbd2589d25b654ee9fb35ed0d3e5f19c5c5403e8e6a195c70f0577a" \
        b" for side 12345678abcdabcd\n"


def test_no_relay():
    c = mock.Mock()
    alsoProvides(c, IDilationConnector)
    p0 = mock.Mock()
    c.build_protocol = mock.Mock(return_value=p0)
    relay_handshake = None
    f = OutboundConnectionFactory(c, relay_handshake, "desc")
    addr = object()
    p = f.buildProtocol(addr)
    assert p is p0
    assert c.mock_calls == [mock.call.build_protocol(addr, "desc")]
    assert p.mock_calls == []
    assert p.factory is f


def test_with_relay():
    c = mock.Mock()
    alsoProvides(c, IDilationConnector)
    p0 = mock.Mock()
    c.build_protocol = mock.Mock(return_value=p0)
    relay_handshake = b"relay handshake"
    f = OutboundConnectionFactory(c, relay_handshake, "desc")
    addr = object()
    p = f.buildProtocol(addr)
    assert p is p0
    assert c.mock_calls == [mock.call.build_protocol(addr, "desc")]
    assert p.mock_calls == [mock.call.use_relay(relay_handshake)]
    assert p.factory is f


def test_build_inbound():
    c = mock.Mock()
    alsoProvides(c, IDilationConnector)
    p0 = mock.Mock()
    c.build_protocol = mock.Mock(return_value=p0)
    f = InboundConnectionFactory(c)
    addr = IPv4Address("TCP", "1.2.3.4", 55)
    p = f.buildProtocol(addr)
    assert p is p0
    assert c.mock_calls == [mock.call.build_protocol(addr, "<-tcp:1.2.3.4:55")]
    assert p.factory is f


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
    h.side = "abcd1234abcd5678"
    h.role = role
    c = Connector(h.dilation_key, h.relay, h.manager, h.reactor, h.eq,
                  not listen, h.tor, timing, h.side, h.role)
    return c, h


def test_build_connector():
    c, h = make_connector()
    c, h = make_connector(relay="tcp:host:1234")


def test_connection_abilities():
    assert Connector.get_connection_abilities() == \
                     [{"type": "direct-tcp-v1"},
                      {"type": "relay-v1"},
                      ]


@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
def test_build_noise():
    build_noise()


def test_build_protocol_leader():
    c, h = make_connector(role=roles.LEADER)
    n0 = mock.Mock()
    p0 = mock.Mock()
    addr = object()
    with mock.patch("wormhole._dilation.connector.build_noise",
                    return_value=n0) as bn:
        with mock.patch("wormhole._dilation.connector.DilatedConnectionProtocol",
                        return_value=p0) as dcp:
            p = c.build_protocol(addr, "desc")
    assert bn.mock_calls == [mock.call()]
    assert n0.mock_calls == [mock.call.set_psks(h.dilation_key),
                                     mock.call.set_as_initiator()]
    assert p is p0
    assert dcp.mock_calls == \
                     [mock.call(h.eq, h.role, "desc", c, n0,
                                PROLOGUE_LEADER, PROLOGUE_FOLLOWER)]

def test_build_protocol_follower():
    c, h = make_connector(role=roles.FOLLOWER)
    n0 = mock.Mock()
    p0 = mock.Mock()
    addr = object()
    with mock.patch("wormhole._dilation.connector.build_noise",
                    return_value=n0) as bn:
        with mock.patch("wormhole._dilation.connector.DilatedConnectionProtocol",
                        return_value=p0) as dcp:
            p = c.build_protocol(addr, "desc")
    assert bn.mock_calls == [mock.call()]
    assert n0.mock_calls == [mock.call.set_psks(h.dilation_key),
                                     mock.call.set_as_responder()]
    assert p is p0
    assert dcp.mock_calls == \
                     [mock.call(h.eq, h.role, "desc", c, n0,
                                PROLOGUE_FOLLOWER, PROLOGUE_LEADER)]

def test_start_stop():
    c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
    c.start()
    # no relays, so it publishes no hints
    assert h.manager.mock_calls == []
    # and no listener, so nothing happens until we provide a hint
    c.stop()
    # we stop while we're connecting, so no connections must be stopped

def test_empty():
    c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()
    # no relays, so it publishes no hints
    assert h.manager.mock_calls == []
    # and no listener, so nothing happens until we provide a hint
    assert c._schedule_connection.mock_calls == []
    c.stop()

def test_basic():
    c, h = make_connector(listen=False, relay=None, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()
    # no relays, so it publishes no hints
    assert h.manager.mock_calls == []
    # and no listener, so nothing happens until we provide a hint
    assert c._schedule_connection.mock_calls == []

    hint = DirectTCPV1Hint("foo", 55, 0.0)
    c.got_hints([hint])

    # received hints don't get published
    assert h.manager.mock_calls == [mock.call._hint_status([DilationHint(url='foo:55', is_direct=True)])]
    # they just schedule a connection
    assert c._schedule_connection.mock_calls == \
                     [mock.call(0.0, DirectTCPV1Hint("foo", 55, 0.0),
                                is_relay=False)]

def test_listen_addresses():
    c, h = make_connector(listen=True, role=roles.LEADER)
    with mock.patch("wormhole.ipaddrs.find_addresses",
                    return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]):
        assert c._get_listener_addresses() == \
                         ["1.2.3.4", "5.6.7.8"]
    with mock.patch("wormhole.ipaddrs.find_addresses",
                    return_value=["127.0.0.1"]):
        # some test hosts, including the appveyor VMs, *only* have
        # 127.0.0.1, and the tests will hang badly if we remove it.
        assert c._get_listener_addresses() == ["127.0.0.1"]

def test_listen():
    c, h = make_connector(listen=True, role=roles.LEADER)
    c._start_listener = mock.Mock()
    with mock.patch("wormhole.ipaddrs.find_addresses",
                    return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]):
        c.start()
    assert c._start_listener.mock_calls == \
                     [mock.call(["1.2.3.4", "5.6.7.8"])]

def test_start_listen():
    c, h = make_connector(listen=True, role=roles.LEADER)
    ep = mock.Mock()
    d = Deferred()
    ep.listen = mock.Mock(return_value=d)
    with mock.patch("wormhole._dilation.connector.serverFromString",
                    return_value=ep) as sfs:
        c._start_listener(["1.2.3.4", "5.6.7.8"])
    assert sfs.mock_calls == [mock.call(h.reactor, "tcp:0")]
    lp = mock.Mock()
    host = mock.Mock()
    host.port = 66
    lp.getHost = mock.Mock(return_value=host)
    d.callback(lp)
    assert h.manager.mock_calls == \
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
                                            ])]

def test_schedule_connection_no_relay():
    c, h = make_connector(listen=True, role=roles.LEADER)
    hint = DirectTCPV1Hint("foo", 55, 0.0)
    ep = mock.Mock()
    with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                    side_effect=[ep]) as efho:
        c._schedule_connection(0.0, hint, False)
    assert efho.mock_calls == [mock.call(hint, h.tor, h.reactor)]
    assert ep.mock_calls == []
    d = Deferred()
    ep.connect = mock.Mock(side_effect=[d])
    # direct hints are scheduled for T+0.0
    f = mock.Mock()
    with mock.patch("wormhole._dilation.connector.OutboundConnectionFactory",
                    return_value=f) as ocf:
        h.clock.advance(1.0)
    assert ocf.mock_calls == [mock.call(c, None, "->tcp:foo:55")]
    assert ep.connect.mock_calls == [mock.call(f)]
    p = mock.Mock()
    d.callback(p)
    assert p.mock_calls == \
                     [mock.call.when_disconnected(),
                      mock.call.when_disconnected().addCallback(c._pending_connections.discard)]

def test_schedule_connection_relay():
    c, h = make_connector(listen=True, role=roles.LEADER)
    hint = DirectTCPV1Hint("foo", 55, 0.0)
    ep = mock.Mock()
    with mock.patch("wormhole._dilation.connector.endpoint_from_hint_obj",
                    side_effect=[ep]) as efho:
        c._schedule_connection(0.0, hint, True)
    assert efho.mock_calls == [mock.call(hint, h.tor, h.reactor)]
    assert ep.mock_calls == []
    d = Deferred()
    ep.connect = mock.Mock(side_effect=[d])
    # direct hints are scheduled for T+0.0
    f = mock.Mock()
    with mock.patch("wormhole._dilation.connector.OutboundConnectionFactory",
                    return_value=f) as ocf:
        h.clock.advance(1.0)
    handshake = build_sided_relay_handshake(h.dilation_key, h.side)
    assert ocf.mock_calls == [mock.call(c, handshake, "->relay:tcp:foo:55")]

def test_listen_but_tor():
    c, h = make_connector(listen=True, tor=True, role=roles.LEADER)
    with mock.patch("wormhole.ipaddrs.find_addresses",
                    return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]) as fa:
        c.start()
    # don't even look up addresses
    assert fa.mock_calls == []
    # no relays and the listener isn't ready yet, so no hints yet
    assert h.manager.mock_calls == []

def test_no_listen():
    c, h = make_connector(listen=False, tor=False, role=roles.LEADER)
    with mock.patch("wormhole.ipaddrs.find_addresses",
                    return_value=["127.0.0.1", "1.2.3.4", "5.6.7.8"]) as fa:
        c.start()
    # don't even look up addresses
    assert fa.mock_calls == []
    assert h.manager.mock_calls == []

def test_relay_delay():
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
    assert c._schedule_connection.mock_calls == \
                     [mock.call(0.0, hint1, is_relay=False),
                      mock.call(0.0, hint2, is_relay=False),
                      mock.call(c.RELAY_DELAY, hint3.hints[0], is_relay=True),
                      ]

def test_initial_relay():
    c, h = make_connector(listen=False, relay="tcp:foo:55", role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()
    assert h.manager.mock_calls == [
        mock.call.send_hints([
            {
                "type": "relay-v1",
                "hints": [
                    {
                        "type": "direct-tcp-v1",
                        "hostname": "foo",
                        "port": 55,
                        "priority": 0.0
                    },
                ],
            },
        ]),
        mock.call._hint_status([DilationHint(url='foo:55', is_direct=False)]),
    ]
    assert c._schedule_connection.mock_calls == \
                     [mock.call(0.0, DirectTCPV1Hint("foo", 55, 0.0),
                                is_relay=True)]

def test_tor_no_manager():
    # tor hints should be ignored if we don't have a Tor manager to use them
    c, h = make_connector(listen=False, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()
    hint = TorTCPV1Hint("foo", 55, 0.0)
    c.got_hints([hint])
    assert h.manager.mock_calls == [
        mock.call._hint_status([]),
    ]
    assert c._schedule_connection.mock_calls == []

def test_tor_with_manager():
    # tor hints should be processed if we do have a Tor manager
    c, h = make_connector(listen=False, tor=True, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()
    hint = TorTCPV1Hint("foo", 55, 0.0)
    c.got_hints([hint])
    assert c._schedule_connection.mock_calls == \
                     [mock.call(0.0, hint, is_relay=False)]

def test_priorities():
    # given two hints with different priorities, we should somehow prefer
    # one. This is a placeholder to fill in once we implement priorities.
    pass


def test_one_leader():
    c, h = make_connector(listen=True, role=roles.LEADER)
    lp = mock.Mock()

    def start_listener(addresses):
        c._listeners.add(lp)
    c._start_listener = start_listener
    c._schedule_connection = mock.Mock()
    c.start()
    assert c._listeners == {lp}

    p1 = mock.Mock()  # DilatedConnectionProtocol instance
    c.add_candidate(p1)
    assert h.manager.mock_calls == []
    h.eq.flush_sync()
    assert h.manager.mock_calls == [mock.call.connector_connection_made(p1)]
    assert p1.mock_calls == \
                     [mock.call.select(h.manager),
                      mock.call.send_record(KCM())]
    assert lp.mock_calls[0] == mock.call.stopListening()
    # stop_listeners() uses a DeferredList, so we ignore the second call

def test_one_follower():
    c, h = make_connector(listen=True, role=roles.FOLLOWER)
    lp = mock.Mock()

    def start_listener(addresses):
        c._listeners.add(lp)
    c._start_listener = start_listener
    c._schedule_connection = mock.Mock()
    c.start()
    assert c._listeners == {lp}

    p1 = mock.Mock()  # DilatedConnectionProtocol instance
    c.add_candidate(p1)
    assert h.manager.mock_calls == []
    h.eq.flush_sync()
    assert h.manager.mock_calls == [mock.call.connector_connection_made(p1)]
    # just like LEADER, but follower doesn't send KCM now (it sent one
    # earlier, to tell the leader that this connection looks viable)
    assert p1.mock_calls == \
                     [mock.call.select(h.manager)]
    assert lp.mock_calls[0] == mock.call.stopListening()
    # stop_listeners() uses a DeferredList, so we ignore the second call

# TODO: make sure a pending connection is abandoned when the listener
# answers successfully

# TODO: make sure a second pending connection is abandoned when the first
# connection succeeds

def test_late():
    c, h = make_connector(listen=False, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()

    p1 = mock.Mock()  # DilatedConnectionProtocol instance
    c.add_candidate(p1)
    assert h.manager.mock_calls == []
    h.eq.flush_sync()
    assert h.manager.mock_calls == [mock.call.connector_connection_made(p1)]
    clear_mock_calls(h.manager)
    assert p1.mock_calls == \
                     [mock.call.select(h.manager),
                      mock.call.send_record(KCM())]

    # late connection is ignored
    p2 = mock.Mock()
    c.add_candidate(p2)
    assert h.manager.mock_calls == []

# make sure an established connection is dropped when stop() is called
def test_stop():
    c, h = make_connector(listen=False, role=roles.LEADER)
    c._schedule_connection = mock.Mock()
    c.start()

    p1 = mock.Mock()  # DilatedConnectionProtocol instance
    c.add_candidate(p1)
    assert h.manager.mock_calls == []
    h.eq.flush_sync()
    assert p1.mock_calls == \
                     [mock.call.select(h.manager),
                      mock.call.send_record(KCM())]
    assert h.manager.mock_calls == [mock.call.connector_connection_made(p1)]

    c.stop()


def test_describe_inbound():
    assert describe_inbound(HostnameAddress("example.com", 1234)) == \
                     "<-tcp:example.com:1234"
    assert describe_inbound(IPv4Address("TCP", "1.2.3.4", 1234)) == \
                     "<-tcp:1.2.3.4:1234"
    assert describe_inbound(IPv6Address("TCP", "::1", 1234)) == \
                     "<-tcp:[::1]:1234"
    other = "none-of-the-above"
    assert describe_inbound(other) == f"<-{other!r}"
