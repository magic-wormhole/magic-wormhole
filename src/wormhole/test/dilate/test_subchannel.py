from unittest import mock
from zope.interface import directlyProvides, implementer
from twisted.internet.interfaces import ITransport, IHalfCloseableProtocol, IProtocol
from twisted.internet.error import ConnectionDone
from ..._interfaces import IDilationManager
from ..._dilation.subchannel import (SubChannel,
                                     _WormholeAddress, SubchannelAddress,
                                     AlreadyClosedError,
                                     NormalCloseUsedOnHalfCloseable,
                                     SubchannelDemultiplex,
                                     UnexpectedSubprotocol)
from ..._dilation.manager import Once
from .common import mock_manager
import pytest
from hypothesis import given
from hypothesis.strategies import text, lists


def make_sc(set_protocol=True, half_closeable=False):
    scid = 4
    hostaddr = _WormholeAddress()
    peeraddr = SubchannelAddress("proto")
    m = mock_manager()
    sc = SubChannel(scid, m, hostaddr, peeraddr)
    p = mock.Mock()
    if half_closeable:
        directlyProvides(p, IHalfCloseableProtocol)
    if set_protocol:
        sc._set_protocol(p)
    return sc, m, scid, hostaddr, peeraddr, p


def test_subchannel_once():
    o = Once(ValueError)
    o()
    with pytest.raises(ValueError):
        o()


def test_subchannel_create():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()
    assert ITransport.providedBy(sc)
    assert m.mock_calls == []
    assert sc.getHost() is hostaddr
    assert sc.getPeer() is peeraddr


def test_subchannel_write():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    sc.write(b"data")
    assert m.mock_calls == [mock.call.send_data(scid, b"data")]
    m.mock_calls[:] = []
    sc.writeSequence([b"more", b"data"])
    assert m.mock_calls == [mock.call.send_data(scid, b"moredata")]


def test_subchannel_write_when_closing():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    sc.loseConnection()
    assert m.mock_calls == [mock.call.send_close(scid)]
    m.mock_calls[:] = []

    with pytest.raises(AlreadyClosedError) as e:
        sc.write(b"data")
    assert str(e.value) == "write not allowed on closed subchannel"


def test_subchannel_local_close():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    sc.loseConnection()
    assert m.mock_calls == [mock.call.send_close(scid)]
    m.mock_calls[:] = []

    # late arriving data is still delivered
    sc.remote_data(b"late")
    assert p.mock_calls == [mock.call.dataReceived(b"late")]
    p.mock_calls[:] = []

    sc.remote_close()
    assert_connectionDone(p.mock_calls)


def test_subchannel_local_double_close():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    sc.loseConnection()
    assert m.mock_calls == [mock.call.send_close(scid)]
    m.mock_calls[:] = []

    with pytest.raises(AlreadyClosedError) as e:
        sc.loseConnection()
    assert str(e.value) == "loseConnection not allowed on closed subchannel"

def assert_connectionDone(mock_calls):
    assert len(mock_calls) == 1
    assert mock_calls[0][0] == "connectionLost"
    assert len(mock_calls[0][1]) == 1
    assert isinstance(mock_calls[0][1][0], ConnectionDone)


def test_subchannel_remote_close():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()
    sc.remote_close()
    assert m.mock_calls == [mock.call.send_close(scid),
                                    mock.call.subchannel_closed(scid, sc)]
    assert_connectionDone(p.mock_calls)


def test_subchannel_data():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()
    sc.remote_data(b"data")
    assert p.mock_calls == [mock.call.dataReceived(b"data")]
    p.mock_calls[:] = []
    sc.remote_data(b"not")
    sc.remote_data(b"coalesced")
    assert p.mock_calls == [mock.call.dataReceived(b"not"),
                                    mock.call.dataReceived(b"coalesced"),
                                    ]


def test_subchannel_data_before_open():
    sc, m, scid, hostaddr, peeraddr, p = make_sc(set_protocol=False)
    sc.remote_data(b"data1")
    sc.remote_data(b"data2")
    assert p.mock_calls == []
    sc._set_protocol(p)
    sc._deliver_queued_data()
    assert p.mock_calls == [mock.call.dataReceived(b"data1"),
                                    mock.call.dataReceived(b"data2")]
    p.mock_calls[:] = []
    sc.remote_data(b"more")
    assert p.mock_calls == [mock.call.dataReceived(b"more")]


def test_subchannel_close_before_open():
    sc, m, scid, hostaddr, peeraddr, p = make_sc(set_protocol=False)
    sc.remote_close()
    assert p.mock_calls == []
    sc._set_protocol(p)
    sc._deliver_queued_data()
    assert_connectionDone(p.mock_calls)


def test_subchannel_producer():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    sc.pauseProducing()
    assert m.mock_calls == [mock.call.subchannel_pauseProducing(sc)]
    m.mock_calls[:] = []
    sc.resumeProducing()
    assert m.mock_calls == [mock.call.subchannel_resumeProducing(sc)]
    m.mock_calls[:] = []
    sc.stopProducing()
    assert m.mock_calls == [mock.call.subchannel_stopProducing(sc)]
    m.mock_calls[:] = []


def test_subchannel_consumer():
    sc, m, scid, hostaddr, peeraddr, p = make_sc()

    # TODO: more, once this is implemented
    sc.registerProducer(None, True)
    sc.unregisterProducer()


def test_halfcloseable_create():
    sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)
    assert ITransport.providedBy(sc)
    assert m.mock_calls == []
    assert sc.getHost() is hostaddr
    assert sc.getPeer() is peeraddr


def test_halfcloseable_local_close():
    sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)

    sc.write(b"data")
    assert m.mock_calls == [mock.call.send_data(scid, b"data")]
    m.mock_calls[:] = []
    sc.writeSequence([b"more", b"data"])
    assert m.mock_calls == [mock.call.send_data(scid, b"moredata")]
    m.mock_calls[:] = []

    sc.remote_data(b"inbound1")
    assert p.mock_calls == [mock.call.dataReceived(b"inbound1")]
    p.mock_calls[:] = []

    with pytest.raises(NormalCloseUsedOnHalfCloseable) as e:
        sc.loseConnection()  # TODO: maybe this shouldn't be an error

    # after a local close, we can't write anymore, but we can still
    # receive data
    sc.loseWriteConnection()  # TODO or loseConnection?
    assert m.mock_calls == [mock.call.send_close(scid)]
    m.mock_calls[:] = []
    assert p.mock_calls == [mock.call.writeConnectionLost()]
    p.mock_calls[:] = []

    with pytest.raises(AlreadyClosedError) as e:
        sc.write(b"data")
    assert str(e.value) == "write not allowed on closed subchannel"

    with pytest.raises(AlreadyClosedError) as e:
        sc.loseWriteConnection()
    assert str(e.value) == "loseConnection not allowed on closed subchannel"

    with pytest.raises(NormalCloseUsedOnHalfCloseable) as e:
        sc.loseConnection()  # TODO: maybe expect AlreadyClosedError

    sc.remote_data(b"inbound2")
    assert p.mock_calls == [mock.call.dataReceived(b"inbound2")]
    p.mock_calls[:] = []

    # the remote end will finally shut down the connection
    sc.remote_close()
    assert m.mock_calls == [mock.call.subchannel_closed(scid, sc)]
    assert p.mock_calls == [mock.call.readConnectionLost()]


def test_halfcloseable_remote_close():
    sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)

    sc.write(b"data")
    assert m.mock_calls == [mock.call.send_data(scid, b"data")]
    m.mock_calls[:] = []

    sc.remote_data(b"inbound1")
    assert p.mock_calls == [mock.call.dataReceived(b"inbound1")]
    p.mock_calls[:] = []

    # after a remote close, we can still write data
    sc.remote_close()
    assert m.mock_calls == []
    assert p.mock_calls == [mock.call.readConnectionLost()]
    p.mock_calls[:] = []

    sc.write(b"out2")
    assert m.mock_calls == [mock.call.send_data(scid, b"out2")]
    m.mock_calls[:] = []

    # and a local close will shutdown the connection
    sc.loseWriteConnection()
    assert m.mock_calls == [mock.call.send_close(scid),
                                    mock.call.subchannel_closed(scid, sc)]
    assert p.mock_calls == [mock.call.writeConnectionLost()]


# these fakes (used by the below tests) should be expanded for use
# elsewhere if required -- or perhaps we can make the "real" objects
# easier to construct for tests.


@implementer(IDilationManager)
class FakeManager:
    pass


@implementer(IProtocol)
class FakeProtocol:
    transport = None
    def makeConnection(self, transport):
        self.transport = transport


class FakeFactory:
    def __init__(self):
        self.builds = []

    def buildProtocol(self, addr):
        self.builds.append(addr)
        return FakeProtocol()


def test_demultiplex():
    """
    Confirm we hold multiple open calls until a listener appears
    """
    demult = SubchannelDemultiplex()

    fake_manager = FakeManager()
    hostaddr = _WormholeAddress()

    addr = SubchannelAddress("subproto")
    t0 = SubChannel(0, fake_manager, hostaddr, addr)
    t1 = SubChannel(1, fake_manager, hostaddr, addr)

    factory = FakeFactory()

    demult._got_open(t0, addr)
    demult._got_open(t1, addr)

    # nothing listening yet, so we should have accumulated two opens
    demult.register("subproto", factory)

    # now we should have gotten two protocol builds (i.e. after we
    # "listen" with our factory)
    assert factory.builds == [addr, addr]


@given(
    lists(
        text(min_size=1),
        min_size=1
    )
)
def test_demultiplex_multiple_protocols(protocols):
    """
    Interleave two separate subprotocols and confirm we hold them open
    until a following listen
    """
    # Hypothesis is giving us a list of subchannel names to open
    # protocols with. It would be best if we had lots of repeats, but
    # trying to _force_ that with assume() makes Hypothesis grumpy
    demult = SubchannelDemultiplex()
    fake_manager = FakeManager()
    hostaddr = _WormholeAddress()
    unique_protocols = set(protocols)

    addresses = {
        nm: SubchannelAddress(nm)
        for nm in unique_protocols
    }
    subchannels = [
        SubChannel(i, fake_manager, hostaddr, addresses[nm])
        for i, nm in enumerate(protocols)
    ]

    for nm, sub in zip(protocols, subchannels):
        demult._got_open(sub, addresses[nm])

    # once we listen, each kind of subprotocol should have represented
    # twice
    factories = {
        nm: FakeFactory()
        for nm in unique_protocols
    }
    for nm, factory in factories.items():
        demult.register(nm, factory)

    # each factory should have had buildProtocol invoked once for each
    # incoming open we had for that name
    for nm, factory in factories.items():
        assert factory.builds == [addresses[nm]] * protocols.count(nm)


def test_unexpected_protocol():
    """
    An incoming subprotocol produces an error if we specify a set of
    expected protocols (and the incoming one is not on the list)
    """
    demult = SubchannelDemultiplex(expected_subprotocols=["foo"])
    fake_manager = FakeManager()
    hostaddr = _WormholeAddress()

    addr = SubchannelAddress("subproto")
    t0 = SubChannel(0, fake_manager, hostaddr, addr)
    with pytest.raises(UnexpectedSubprotocol):
        demult._got_open(t0, addr)
