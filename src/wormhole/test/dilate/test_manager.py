from zope.interface import alsoProvides
from twisted.internet.task import Clock, Cooperator
from twisted.internet.interfaces import IStreamServerEndpoint
from unittest import mock
import pytest
import pytest_twisted

from ...eventual import EventualQueue
from ..._interfaces import ISend, ITerminator, ISubChannel
from ...util import dict_to_bytes
from ..._dilation import roles
from ..._dilation.manager import (Dilator, Manager, make_side,
                                  OldPeerCannotDilateError,
                                  CanOnlyDilateOnceError,
                                  UnknownDilationMessageType,
                                  UnexpectedKCM,
                                  UnknownMessageType, DILATION_VERSIONS)
from ..._dilation.connection import Open, Data, Close, Ack, KCM, Ping, Pong
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
    dil = Dilator(h.reactor, h.eq, h.coop, DILATION_VERSIONS)
    h.terminator = mock.Mock()
    alsoProvides(h.terminator, ITerminator)
    dil.wire(h.send, h.terminator)
    return dil, h


# we should test the interleavings between:
# * application calls w.dilate() and gets back endpoints
# * wormhole gets: dilation key, VERSION, 0-n dilation messages


def test_dilate_first():
    (dil, h) = make_dilator()
    side = object()
    m = mock.Mock()
    m._api = eps = object()
    mm = mock.Mock(side_effect=[m])
    with mock.patch("wormhole._dilation.manager.Manager", mm), \
         mock.patch("wormhole._dilation.manager.make_side",
                    return_value=side):
        eps1 = dil.dilate()
        with pytest.raises(CanOnlyDilateOnceError):
            dil.dilate()
    assert eps1 is eps
    assert mm.mock_calls == [mock.call(h.send, side, None,
                                       h.reactor, h.eq, h.coop, DILATION_VERSIONS, 30.0, None,
                                       False, None, initial_mailbox_status=None)]

    assert m.mock_calls == []

    key = b"key"
    transit_key = object()
    with mock.patch("wormhole._dilation.manager.derive_key",
                    return_value=transit_key) as dk:
        dil.got_key(key)
    assert dk.mock_calls == [mock.call(key, b"dilation-v1", 32)]
    assert m.mock_calls == [mock.call.got_dilation_key(transit_key)]
    clear_mock_calls(m)

    wv = object()
    dil.got_wormhole_versions(wv)
    assert m.mock_calls == [mock.call.got_wormhole_versions(wv)]
    clear_mock_calls(m)

    dm1 = object()
    dm2 = object()
    dil.received_dilate(dm1)
    dil.received_dilate(dm2)
    assert m.mock_calls == [mock.call.received_dilation_message(dm1),
                                    mock.call.received_dilation_message(dm2),
                                    ]
    clear_mock_calls(m)

    stopped_d = mock.Mock()
    m.when_stopped = mock.Mock(return_value=stopped_d)
    dil.stop()
    assert m.mock_calls == [mock.call.stop(),
                                    mock.call.when_stopped(),
                                    ]


def test_dilate_later():
    (dil, h) = make_dilator()
    m = mock.Mock()
    mm = mock.Mock(side_effect=[m])

    key = b"key"
    transit_key = object()
    with mock.patch("wormhole._dilation.manager.derive_key",
                    return_value=transit_key) as dk:
        dil.got_key(key)
    assert dk.mock_calls == [mock.call(key, b"dilation-v1", 32)]

    wv = object()
    dil.got_wormhole_versions(wv)

    dm1 = object()
    dil.received_dilate(dm1)

    assert mm.mock_calls == []

    with mock.patch("wormhole._dilation.manager.Manager", mm):
        dil.dilate()
    assert m.mock_calls == [mock.call.got_dilation_key(transit_key),
                                    mock.call.got_wormhole_versions(wv),
                                    mock.call.received_dilation_message(dm1),
                                    ]
    clear_mock_calls(m)

    dm2 = object()
    dil.received_dilate(dm2)
    assert m.mock_calls == [mock.call.received_dilation_message(dm2),
                                    ]

def test_stop_early():
    (dil, h) = make_dilator()
    # we stop before w.dilate(), so there is no Manager to stop
    dil.stop()
    assert h.terminator.mock_calls == [mock.call.stoppedD()]


@pytest_twisted.ensureDeferred
async def test_peer_cannot_dilate():
    (dil, h) = make_dilator()
    eps = dil.dilate()

    dil.got_key(b"\x01" * 32)
    dil.got_wormhole_versions({})  # missing "can-dilate"
    f = mock.Mock()
    d = eps.connector_for("proto").connect(f)
    h.eq.flush_sync()
    with pytest.raises(OldPeerCannotDilateError):
        await d


@pytest_twisted.ensureDeferred
async def test_disjoint_versions():
    (dil, h) = make_dilator()
    eps = dil.dilate()

    dil.got_key(b"\x01" * 32)
    dil.got_wormhole_versions({"can-dilate": ["-1"]})
    f = mock.Mock()
    d = eps.connector_for("proto").connect(f)
    h.eq.flush_sync()
    with pytest.raises(OldPeerCannotDilateError):
        await d


def test_transit_relay():
    (dil, h) = make_dilator()
    transit_relay_location = object()
    side = object()
    m = mock.Mock()
    mm = mock.Mock(side_effect=[m])
    with mock.patch("wormhole._dilation.manager.Manager", mm), \
         mock.patch("wormhole._dilation.manager.make_side",
                    return_value=side):
        dil.dilate(transit_relay_location)
    assert mm.mock_calls == [mock.call(h.send, side, transit_relay_location,
                                       h.reactor, h.eq, h.coop, DILATION_VERSIONS, 30.0, None,
                                       False, None, initial_mailbox_status=None)]


LEADER = "ff3456abcdef"
FOLLOWER = "123456abcdef"


class ReactorOnlyTime:
    """
    Provide a reactor-like mock that at least has seconds()
    """
    # not ideal, but prior to this the "reactor" below was literally
    # just "object()"

    def seconds(self):
        return 42

    def callLater(self, interval, callable):
        # should return a DelayedCall
        return mock.Mock()


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
    h.reactor = ReactorOnlyTime()
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
         mock.patch("wormhole._dilation.subchannel.SubChannel", h.SubChannel), \
         mock.patch("wormhole._dilation.manager.SubchannelListenerEndpoint",
                    return_value=h.listen_ep):
        m = Manager(h.send, side, h.relay, h.reactor, h.eq, h.coop, DILATION_VERSIONS, 30.0, {})
    h.hostaddr = m._host_addr
    m.got_dilation_key(h.key)
    return m, h


def test_make_side():
    side = make_side()
    assert type(side) is str
    assert len(side) == 2 * 8


def test_create():
    m, h = make_manager()


def test_leader():
    m, h = make_manager(leader=True)
    assert h.send.mock_calls == []
    assert h.Inbound.mock_calls == [mock.call(m, h.hostaddr)]
    assert h.Outbound.mock_calls == [mock.call(m, h.coop)]
    assert h.SubChannel.mock_calls == []
    assert h.inbound.mock_calls == []
    clear_mock_calls(h.inbound)

    m.got_wormhole_versions({"can-dilate": ["ged"]})
    assert h.send.mock_calls == [
        mock.call.send("dilate-0",
                       dict_to_bytes({"type": "please", "side": LEADER, "use-version": "ged"}))
        ]
    clear_mock_calls(h.send)

    # ignore early hints
    m.rx_HINTS({})
    assert h.send.mock_calls == []

    c = mock.Mock()
    connector = mock.Mock(return_value=c)
    with mock.patch("wormhole._dilation.manager.Connector", connector):
        # receiving this PLEASE triggers creation of the Connector
        m.rx_PLEASE({"side": FOLLOWER})
    assert h.send.mock_calls == []
    assert connector.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  LEADER, roles.LEADER),
        ]
    assert c.mock_calls == [mock.call.start()]
    clear_mock_calls(connector, c)

    # now any inbound hints should get passed to our Connector
    with mock.patch("wormhole._dilation.manager.parse_hint",
                    side_effect=["p1", None, "p3"]) as ph:
        m.rx_HINTS({"hints": [1, 2, 3]})
    assert ph.mock_calls == [mock.call(1), mock.call(2), mock.call(3)]
    assert c.mock_calls == [mock.call.got_hints(["p1", "p3"])]
    clear_mock_calls(ph, c)

    # and we send out any (listening) hints from our Connector
    m.send_hints([1, 2])
    assert h.send.mock_calls == [
        mock.call.send("dilate-1",
                       dict_to_bytes({"type": "connection-hints",
                                      "hints": [1, 2]}))
        ]
    clear_mock_calls(h.send)

    # the first successful connection fires when_first_connected(), so
    # the endpoints can activate
    c1 = mock.Mock()
    m.connector_connection_made(c1)

    assert h.inbound.mock_calls == [mock.call.use_connection(c1)]
    assert h.outbound.mock_calls[1:] == [mock.call.use_connection(c1)]
    clear_mock_calls(h.inbound, h.outbound)

    # the Leader making a new outbound channel should get scid=1
    scid1 = 1
    assert m.allocate_subchannel_id() == scid1
    r1 = Open(10, scid1, "proto")  # seqnum=10
    h.outbound.build_record = mock.Mock(return_value=r1)
    m.send_open(scid1, "proto")
    assert h.outbound.mock_calls == [
        mock.call.build_record(Open, scid1, "proto"),
        mock.call.queue_and_send_record(r1),
        ]
    clear_mock_calls(h.outbound)

    r2 = Data(11, scid1, b"data")
    h.outbound.build_record = mock.Mock(return_value=r2)
    m.send_data(scid1, b"data")
    assert h.outbound.mock_calls == [
        mock.call.build_record(Data, scid1, b"data"),
        mock.call.queue_and_send_record(r2),
        ]
    clear_mock_calls(h.outbound)

    r3 = Close(12, scid1)
    h.outbound.build_record = mock.Mock(return_value=r3)
    m.send_close(scid1)
    assert h.outbound.mock_calls == [
        mock.call.build_record(Close, scid1),
        mock.call.queue_and_send_record(r3),
        ]
    clear_mock_calls(h.outbound)

    # ack the OPEN
    m.got_record(Ack(10))
    assert h.outbound.mock_calls == [
        mock.call.handle_ack(10)
        ]
    clear_mock_calls(h.outbound)

    # test that inbound records get acked and routed to Inbound
    h.inbound.is_record_old = mock.Mock(return_value=False)
    scid2 = 2
    o200 = Open(200, scid2, "proto")
    m.got_record(o200)
    assert h.outbound.mock_calls == [
        mock.call.send_if_connected(Ack(200))
        ]
    assert h.inbound.mock_calls == [
        mock.call.is_record_old(o200),
        mock.call.update_ack_watermark(200),
        mock.call.handle_open(scid2, "proto"),
        ]
    clear_mock_calls(h.outbound, h.inbound)

    # old (duplicate) records should provoke new Acks, but not get
    # forwarded
    h.inbound.is_record_old = mock.Mock(return_value=True)
    m.got_record(o200)
    assert h.outbound.mock_calls == [
        mock.call.send_if_connected(Ack(200))
        ]
    assert h.inbound.mock_calls == [
        mock.call.is_record_old(o200),
        ]
    clear_mock_calls(h.outbound, h.inbound)

    # check Data and Close too
    h.inbound.is_record_old = mock.Mock(return_value=False)
    d201 = Data(201, scid2, b"data")
    m.got_record(d201)
    assert h.outbound.mock_calls == [
        mock.call.send_if_connected(Ack(201))
        ]
    assert h.inbound.mock_calls == [
        mock.call.is_record_old(d201),
        mock.call.update_ack_watermark(201),
        mock.call.handle_data(scid2, b"data"),
        ]
    clear_mock_calls(h.outbound, h.inbound)

    c202 = Close(202, scid2)
    m.got_record(c202)
    assert h.outbound.mock_calls == [
        mock.call.send_if_connected(Ack(202))
        ]
    assert h.inbound.mock_calls == [
        mock.call.is_record_old(c202),
        mock.call.update_ack_watermark(202),
        mock.call.handle_close(scid2),
        ]
    clear_mock_calls(h.outbound, h.inbound)

    # Now we lose the connection. The Leader should tell the other side
    # that we're reconnecting.

    m.connector_connection_lost()
    assert h.send.mock_calls == [
        mock.call.send("dilate-2",
                       dict_to_bytes({"type": "reconnect"}))
        ]
    assert h.inbound.mock_calls == [
        mock.call.stop_using_connection()
        ]
    assert h.outbound.mock_calls == [
        mock.call.stop_using_connection()
        ]
    clear_mock_calls(h.send, h.inbound, h.outbound)

    # leader does nothing (stays in FLUSHING) until the follower acks by
    # sending RECONNECTING

    # inbound hints should be ignored during FLUSHING
    with mock.patch("wormhole._dilation.manager.parse_hint",
                    return_value=None) as ph:
        m.rx_HINTS({"hints": [1, 2, 3]})
    assert ph.mock_calls == []  # ignored

    c2 = mock.Mock()
    connector2 = mock.Mock(return_value=c2)
    with mock.patch("wormhole._dilation.manager.Connector", connector2):
        # this triggers creation of a new Connector
        m.rx_RECONNECTING()
    assert h.send.mock_calls == []
    assert connector2.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  LEADER, roles.LEADER),
        ]
    assert c2.mock_calls == [mock.call.start()]
    clear_mock_calls(connector2, c2)

    assert h.inbound.mock_calls == []
    assert h.outbound.mock_calls == []

    # and a new connection should re-register with Inbound/Outbound,
    # which are responsible for re-sending unacked queued messages
    c3 = mock.Mock()
    m.connector_connection_made(c3)

    assert h.inbound.mock_calls == [mock.call.use_connection(c3)]
    assert h.outbound.mock_calls[1:] == [mock.call.use_connection(c3)]
    clear_mock_calls(h.inbound, h.outbound)


def test_follower():
    m, h = make_manager(leader=False)

    m.got_wormhole_versions({"can-dilate": ["ged"]})
    assert h.send.mock_calls == [
        mock.call.send("dilate-0",
                       dict_to_bytes({"type": "please", "side": FOLLOWER, "use-version": "ged"}))
        ]
    clear_mock_calls(h.send)
    clear_mock_calls(h.inbound)

    c = mock.Mock()
    connector = mock.Mock(return_value=c)
    with mock.patch("wormhole._dilation.manager.Connector", connector):
        # receiving this PLEASE triggers creation of the Connector
        m.rx_PLEASE({"side": LEADER})
    assert h.send.mock_calls == []
    assert connector.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  FOLLOWER, roles.FOLLOWER),
        ]
    assert c.mock_calls == [mock.call.start()]
    clear_mock_calls(connector, c)

    # get connected, then lose the connection
    c1 = mock.Mock()
    m.connector_connection_made(c1)
    assert h.inbound.mock_calls == [mock.call.use_connection(c1)]
    assert h.outbound.mock_calls == [mock.call.use_connection(c1)]
    clear_mock_calls(h.inbound, h.outbound)

    # now lose the connection. As the follower, we don't notify the
    # leader, we just wait for them to notice
    m.connector_connection_lost()
    assert h.send.mock_calls == []
    assert h.inbound.mock_calls == [
        mock.call.stop_using_connection()
        ]
    assert h.outbound.mock_calls == [
        mock.call.stop_using_connection()
        ]
    clear_mock_calls(h.send, h.inbound, h.outbound)

    # now we get a RECONNECT: we should send RECONNECTING
    c2 = mock.Mock()
    connector2 = mock.Mock(return_value=c2)
    with mock.patch("wormhole._dilation.manager.Connector", connector2):
        m.rx_RECONNECT()
    assert h.send.mock_calls == [
        mock.call.send("dilate-1",
                       dict_to_bytes({"type": "reconnecting"}))
        ]
    assert connector2.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  FOLLOWER, roles.FOLLOWER),
        ]
    assert c2.mock_calls == [mock.call.start()]
    clear_mock_calls(connector2, c2)

    # while we're trying to connect, we get told to stop again, so we
    # should abandon the connection attempt and start another
    c3 = mock.Mock()
    connector3 = mock.Mock(return_value=c3)
    with mock.patch("wormhole._dilation.manager.Connector", connector3):
        m.rx_RECONNECT()
    assert c2.mock_calls == [mock.call.stop()]
    assert connector3.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  FOLLOWER, roles.FOLLOWER),
        ]
    assert c3.mock_calls == [mock.call.start()]
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
    assert c3.mock_calls == [mock.call.disconnect()]
    assert connector4.mock_calls == [
        mock.call(b"\x00" * 32, None, m, h.reactor, h.eq,
                  False,  # no_listen
                  None,  # tor
                  None,  # timing
                  FOLLOWER, roles.FOLLOWER),
        ]
    assert c4.mock_calls == [mock.call.start()]
    clear_mock_calls(c3, connector4, c4)


def test_mirror():
    # receive a PLEASE with the same side as us: shouldn't happen
    m, h = make_manager(leader=True)

    m.start()
    clear_mock_calls(h.send)
    with pytest.raises(ValueError) as f:
        m.rx_PLEASE({"side": LEADER})
    assert str(f.value) == "their side shouldn't be equal: reflection?"


def test_ping_pong(observe_errors):
    m, h = make_manager(leader=False)

    m.got_record(KCM())
    observe_errors.flush(UnexpectedKCM)

    m.got_record(Ping(1))
    assert h.outbound.mock_calls == \
                     [mock.call.send_if_connected(Pong(1))]
    clear_mock_calls(h.outbound)

    m.got_record("not recognized")
    e = observe_errors.flush(UnknownMessageType)
    assert len(e) == 1
    assert str(e[0].value) == "not recognized"

    m.send_ping(2, lambda _: None)
    assert h.outbound.mock_calls == \
                     [mock.call.send_if_connected(Pong(2))]
    clear_mock_calls(h.outbound)

    # sort of low-level; what does this look like to API user?
    class FakeError(Exception):
        pass

    def cause_error(_):
        raise FakeError()
    m.send_ping(3, cause_error)
    assert h.outbound.mock_calls == \
                     [mock.call.send_if_connected(Pong(3))]
    clear_mock_calls(h.outbound)
    with pytest.raises(FakeError):
        m.got_record(Pong(3))


def test_subchannel():
    m, h = make_manager(leader=True)
    clear_mock_calls(h.inbound)
    sc = object()

    m.subchannel_pauseProducing(sc)
    assert h.inbound.mock_calls == [
        mock.call.subchannel_pauseProducing(sc)]
    clear_mock_calls(h.inbound)

    m.subchannel_resumeProducing(sc)
    assert h.inbound.mock_calls == [
        mock.call.subchannel_resumeProducing(sc)]
    clear_mock_calls(h.inbound)

    m.subchannel_stopProducing(sc)
    assert h.inbound.mock_calls == [
        mock.call.subchannel_stopProducing(sc)]
    clear_mock_calls(h.inbound)

    p = object()
    streaming = object()

    m.subchannel_registerProducer(sc, p, streaming)
    assert h.outbound.mock_calls == [
        mock.call.subchannel_registerProducer(sc, p, streaming)]
    clear_mock_calls(h.outbound)

    m.subchannel_unregisterProducer(sc)
    assert h.outbound.mock_calls == [
        mock.call.subchannel_unregisterProducer(sc)]
    clear_mock_calls(h.outbound)

    m.subchannel_closed(4, sc)
    assert h.inbound.mock_calls == [
        mock.call.subchannel_closed(4, sc)]
    assert h.outbound.mock_calls == [
        mock.call.subchannel_closed(4, sc)]
    clear_mock_calls(h.inbound, h.outbound)


def test_unknown_message(observe_errors):
    # receive a PLEASE with the same side as us: shouldn't happen
    m, h = make_manager(leader=True)
    m.start()

    m.received_dilation_message(dict_to_bytes(dict(type="unknown")))
    observe_errors.flush(UnknownDilationMessageType)

# TODO: test transit relay is used
