from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.task import Clock
from twisted.python.failure import Failure
from twisted.internet.interfaces import IProtocolFactory
import pytest
import pytest_twisted

from ...eventual import EventualQueue
from ...observer import OneShotObserver
from ..._dilation.subchannel import (SubchannelConnectorEndpoint,
                                     SubchannelListenerEndpoint,
                                     SubchannelListeningPort,
                                     _WormholeAddress, SubchannelAddress)
from .common import mock_manager


class CannotDilateError(Exception):
    pass


def OFFassert_makeConnection(mock_calls):
    assert len(mock_calls) == 1
    assert mock_calls[0][0] == "makeConnection"
    assert len(mock_calls[0][1]) == 1
    return mock_calls[0][1][0]


@pytest_twisted.ensureDeferred
async def test_connector_early_succeed():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p = mock.Mock()
    t = mock.Mock()
    f.buildProtocol = mock.Mock(return_value=p)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t) as sc:
        d = ep.connect(f)
        eq.flush_sync()
        assert not d.called
        m._main_channel.fire(None)
        eq.flush_sync()

    proto = await d
    assert proto is p
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr)]
    assert sc.mock_calls == [mock.call(0, m, hostaddr, peeraddr)]
    assert t.mock_calls == [mock.call._set_protocol(p)]
    assert p.mock_calls == [mock.call.makeConnection(t)]


@pytest_twisted.ensureDeferred
async def test_connector_early_fail():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p = mock.Mock()
    t = mock.Mock()
    f.buildProtocol = mock.Mock(return_value=p)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t) as sc:
        d = ep.connect(f)
        eq.flush_sync()
        assert not d.called
        m._main_channel.error(Failure(CannotDilateError()))
        eq.flush_sync()

    with pytest.raises(CannotDilateError):
        await d
    assert f.buildProtocol.mock_calls == []
    assert sc.mock_calls == []
    assert t.mock_calls == []


@pytest_twisted.ensureDeferred
async def test_connector_late_succeed():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    m._main_channel.fire(None)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p = mock.Mock()
    t = mock.Mock()
    f.buildProtocol = mock.Mock(return_value=p)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t) as sc:
        d = ep.connect(f)
        eq.flush_sync()

    proto = await d
    assert proto is p
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr)]
    assert sc.mock_calls == [mock.call(0, m, hostaddr, peeraddr)]
    assert t.mock_calls == [mock.call._set_protocol(p)]
    assert p.mock_calls == [mock.call.makeConnection(t)]


@pytest_twisted.ensureDeferred
async def test_connector_late_fail():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    m._main_channel.error(Failure(CannotDilateError()))

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p = mock.Mock()
    t = mock.Mock()
    f.buildProtocol = mock.Mock(return_value=p)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t) as sc:
        d = ep.connect(f)
        eq.flush_sync()

    with pytest.raises(CannotDilateError):
        await d
    assert f.buildProtocol.mock_calls == []
    assert sc.mock_calls == []
    assert t.mock_calls == []


@pytest_twisted.ensureDeferred
async def test_listener_early_succeed():
    # listen, main_channel_ready, got_open, got_open
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelListenerEndpoint(m, hostaddr, eq)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p1 = mock.Mock()
    p2 = mock.Mock()
    f.buildProtocol = mock.Mock(side_effect=[p1, p2])

    d = ep.listen(f)
    eq.flush_sync()
    assert not d.called
    assert f.buildProtocol.mock_calls == []

    m._main_channel.fire(None)
    eq.flush_sync()
    lp = await d
    assert isinstance(lp, SubchannelListeningPort)

    assert lp.getHost() == hostaddr
    # TODO: IListeningPort says we must provide this, but I don't know
    # that anyone would ever call it.
    lp.startListening()

    t1 = mock.Mock()
    peeraddr1 = SubchannelAddress("proto")
    ep._got_open(t1, peeraddr1)

    assert t1.mock_calls == [mock.call._set_protocol(p1),
                                     mock.call._deliver_queued_data()]
    assert p1.mock_calls == [mock.call.makeConnection(t1)]
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr1)]

    t2 = mock.Mock()
    peeraddr2 = SubchannelAddress("proto")
    ep._got_open(t2, peeraddr2)

    assert t2.mock_calls == [mock.call._set_protocol(p2),
                                     mock.call._deliver_queued_data()]
    assert p2.mock_calls == [mock.call.makeConnection(t2)]
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr1),
                                                  mock.call(peeraddr2)]

    lp.stopListening()  # TODO: should this do more?


@pytest_twisted.ensureDeferred
async def test_listener_early_fail():
    # listen, main_channel_fail
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelListenerEndpoint(m, hostaddr, eq)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p1 = mock.Mock()
    p2 = mock.Mock()
    f.buildProtocol = mock.Mock(side_effect=[p1, p2])

    d = ep.listen(f)
    eq.flush_sync()
    assert not d.called

    m._main_channel.error(Failure(CannotDilateError()))
    eq.flush_sync()
    with pytest.raises(CannotDilateError):
        await d
    assert f.buildProtocol.mock_calls == []


@pytest_twisted.ensureDeferred
async def test_listener_late_succeed():
    # main_channel_ready, got_open, listen, got_open
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    ep = SubchannelListenerEndpoint(m, hostaddr, eq)
    m._main_channel.fire(None)

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p1 = mock.Mock()
    p2 = mock.Mock()
    f.buildProtocol = mock.Mock(side_effect=[p1, p2])

    t1 = mock.Mock()
    peeraddr1 = SubchannelAddress("proto")
    ep._got_open(t1, peeraddr1)
    eq.flush_sync()

    assert t1.mock_calls == []
    assert p1.mock_calls == []

    d = ep.listen(f)
    eq.flush_sync()
    lp = await d
    assert isinstance(lp, SubchannelListeningPort)
    assert lp.getHost() == hostaddr
    lp.startListening()

    # TODO: assert makeConnection is called *before* _deliver_queued_data
    assert t1.mock_calls == [mock.call._set_protocol(p1),
                                     mock.call._deliver_queued_data()]
    assert p1.mock_calls == [mock.call.makeConnection(t1)]
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr1)]

    t2 = mock.Mock()
    peeraddr2 = SubchannelAddress("proto")
    ep._got_open(t2, peeraddr2)

    assert t2.mock_calls == [mock.call._set_protocol(p2),
                                     mock.call._deliver_queued_data()]
    assert p2.mock_calls == [mock.call.makeConnection(t2)]
    assert f.buildProtocol.mock_calls == [mock.call(peeraddr1),
                                                  mock.call(peeraddr2)]

    lp.stopListening()  # TODO: should this do more?


@pytest_twisted.ensureDeferred
async def test_listener_late_fail():
    # main_channel_fail, listen
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelListenerEndpoint(m, hostaddr, eq)
    m._main_channel.error(Failure(CannotDilateError()))

    f = mock.Mock()
    f.subprotocol = "proto"
    alsoProvides(f, IProtocolFactory)
    p1 = mock.Mock()
    p2 = mock.Mock()
    f.buildProtocol = mock.Mock(side_effect=[p1, p2])

    d = ep.listen(f)
    eq.flush_sync()
    with pytest.raises(CannotDilateError):
        await d
    assert f.buildProtocol.mock_calls == []
