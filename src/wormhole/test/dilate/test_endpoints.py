from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.task import Clock
from twisted.python.failure import Failure
from twisted.internet.interfaces import IProtocolFactory
from twisted.internet.protocol import Protocol, Factory
import pytest
import pytest_twisted

from ...eventual import EventualQueue
from ...observer import OneShotObserver
from ..._dilation.subchannel import (SubchannelConnectorEndpoint,
                                     SubchannelListenerEndpoint,
                                     SubchannelListeningPort,
                                     SubchannelDemultiplex,
                                     SubChannel,
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
    """
    Call 'connect' on a subchannel client-style endpoint before the
    Dilation channel has been established (and then fake Dilation
    establishment)
    """
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    t = SubChannel(123, m, hostaddr, peeraddr)

    class Simple(Protocol):
        pass

    f = Factory.forProtocol(Simple)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t):
        d = ep.connect(f)
        eq.flush_sync()
        # the Dilation channel has NOT become available yet, so we
        # should not have actually succeeded to connect our protocol
        # until it does
        assert not d.called

    # make the Dilation connection available
    m._main_channel.fire(None)
    eq.flush_sync()

    proto = await d
    assert isinstance(proto, Simple)


@pytest_twisted.ensureDeferred
async def test_connector_early_fail():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    t = SubChannel(123, m, hostaddr, peeraddr)

    class Simple(Protocol):
        pass

    f = Factory.forProtocol(Simple)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t):
        d = ep.connect(f)
        eq.flush_sync()
        # the Dilation channel has NOT become available yet, so we
        # should not have actually succeeded to connect our protocol
        # until it does
        assert not d.called

    # there has been some kind of error establishing the Dilation
    # channel, and so it has failed.
    m._main_channel.error(Failure(CannotDilateError()))
    eq.flush_sync()

    with pytest.raises(CannotDilateError):
        await d


@pytest_twisted.ensureDeferred
async def test_connector_late_succeed():
    """
    Same as test_connector_early_succeed except the Dilation channel
    is already available before we call .connect()
    """
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    t = SubChannel(123, m, hostaddr, peeraddr)
    # make the Dilation connection available
    m._main_channel.fire(None)

    class Simple(Protocol):
        pass

    f = Factory.forProtocol(Simple)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t):
        d = ep.connect(f)
        eq.flush_sync()

    proto = await d
    assert isinstance(proto, Simple)


@pytest_twisted.ensureDeferred
async def test_connector_late_fail():
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelConnectorEndpoint("proto", m, hostaddr, eq)
    t = SubChannel(123, m, hostaddr, peeraddr)
    # there has been some kind of error establishing the Dilation
    # channel, and so it has failed.
    m._main_channel.error(Failure(CannotDilateError()))

    class Simple(Protocol):
        pass

    f = Factory.forProtocol(Simple)
    with mock.patch("wormhole._dilation.subchannel.SubChannel",
                    return_value=t):
        d = ep.connect(f)
        eq.flush_sync()

    eq.flush_sync()

    with pytest.raises(CannotDilateError):
        await d


# refactor code could make more testable? Demultiplex thing...
@pytest_twisted.ensureDeferred
async def test_listener_early_succeed():
    # listen, main_channel_ready, got_open, got_open
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    m._host_addr = hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    demux = SubchannelDemultiplex()
    m._subprotocol_Factories = demux
    ep = SubchannelListenerEndpoint("proto", m)

    class Simple(Protocol):
        pass

    class CollectBuilds(Factory):
        protocols = []

        def buildProtocol(self, addr):
            p = Factory.buildProtocol(self, addr)
            self.protocols.append(p)
            return p

    f = CollectBuilds()
    f.protocol = Simple
    demux.register("proto", f)

    d = ep.listen(f)
    eq.flush_sync()
    assert not d.called

    m._main_channel.fire(None)
    eq.flush_sync()
    lp = await d
    assert isinstance(lp, SubchannelListeningPort)

    assert lp.getHost() == hostaddr
    # TODO: IListeningPort says we must provide this, but I don't know
    # that anyone would ever call it.
    lp.startListening()

    peeraddr1 = SubchannelAddress("proto")
    t1 = SubChannel(1234, m, hostaddr, peeraddr1)
    demux._got_open(t1, peeraddr1)

    # prior asserts here all about mocks -- basically just testing
    # that the buildProtocol was called. So now we collect that
    # ourselves, but anything else interesting we might check? Can a
    # Twisted API get the protocol from somewhere?
    assert len(CollectBuilds.protocols) == 1, "expected precisely one listener protocol"
    assert all(isinstance(p, Simple) for p in CollectBuilds.protocols)

    peeraddr2 = SubchannelAddress("proto")
    t2 = SubChannel(5678, m, hostaddr, peeraddr2)
    demux._got_open(t2, peeraddr2)

    # as above, anything else interesting we should assert?
    assert len(CollectBuilds.protocols) == 2, "expected precisely one listener protocol"
    assert all(isinstance(p, Simple) for p in CollectBuilds.protocols)

    lp.stopListening()  # TODO: should this do more?


@pytest_twisted.ensureDeferred
async def test_listener_early_fail():
    # listen, main_channel_fail
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelListenerEndpoint("proto", m)

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
    # listen, main_channel_ready, got_open, got_open
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    m._host_addr = hostaddr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    demux = SubchannelDemultiplex()
    m._subprotocol_Factories = demux
    ep = SubchannelListenerEndpoint("proto", m)
    # XXX only real difference between "early" / "late" tests is when we call this -- use a test-variant instead?
    m._main_channel.fire(None)

    class Simple(Protocol):
        pass

    class CollectBuilds(Factory):
        protocols = []

        def buildProtocol(self, addr):
            p = Factory.buildProtocol(self, addr)
            self.protocols.append(p)
            return p

    f = CollectBuilds()
    f.protocol = Simple
    demux.register("proto", f)

    d = ep.listen(f)
    eq.flush_sync()
    lp = await d
    assert isinstance(lp, SubchannelListeningPort)

    assert lp.getHost() == hostaddr
    # TODO: IListeningPort says we must provide this, but I don't know
    # that anyone would ever call it.
    lp.startListening()

    peeraddr1 = SubchannelAddress("proto")
    t1 = SubChannel(1234, m, hostaddr, peeraddr1)
    demux._got_open(t1, peeraddr1)

    # prior asserts here all about mocks -- basically just testing
    # that the buildProtocol was called. So now we collect that
    # ourselves, but anything else interesting we might check? Can a
    # Twisted API get the protocol from somewhere?
    assert len(CollectBuilds.protocols) == 1, "expected precisely one listener protocol"
    assert all(isinstance(p, Simple) for p in CollectBuilds.protocols)

    peeraddr2 = SubchannelAddress("proto")
    t2 = SubChannel(5678, m, hostaddr, peeraddr2)
    demux._got_open(t2, peeraddr2)

    # as above, anything else interesting we should assert?
    assert len(CollectBuilds.protocols) == 2, "expected precisely one listener protocol"
    assert all(isinstance(p, Simple) for p in CollectBuilds.protocols)

    lp.stopListening()  # TODO: should this do more?


@pytest_twisted.ensureDeferred
async def test_listener_late_fail():
    # main_channel_fail, listen
    m = mock_manager()
    m.allocate_subchannel_id = mock.Mock(return_value=0)
    m._host_addr = _WormholeAddress()
    eq = EventualQueue(Clock())
    m._main_channel = OneShotObserver(eq)
    ep = SubchannelListenerEndpoint("proto", m)
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
