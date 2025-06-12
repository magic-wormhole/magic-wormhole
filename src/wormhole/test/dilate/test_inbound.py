from unittest import mock
from zope.interface import alsoProvides
from ..._interfaces import IDilationManager
from ..._dilation.connection import Open, Data, Close
from ..._dilation.inbound import (Inbound, DuplicateOpenError,
                                  DataForMissingSubchannelError,
                                  CloseForMissingSubchannelError)


def make_inbound():
    m = mock.Mock()
    alsoProvides(m, IDilationManager)
    m._subprotocol_factories = mock.Mock()
    host_addr = object()
    i = Inbound(m, host_addr)
    return i, m, host_addr


def test_seqnum():
    i, m, host_addr = make_inbound()
    r1 = Open(scid=513, seqnum=1, subprotocol="proto")
    r2 = Data(scid=513, seqnum=2, data=b"")
    r3 = Close(scid=513, seqnum=3)
    assert not i.is_record_old(r1)
    assert not i.is_record_old(r2)
    assert not i.is_record_old(r3)

    i.update_ack_watermark(r1.seqnum)
    assert i.is_record_old(r1)
    assert not i.is_record_old(r2)
    assert not i.is_record_old(r3)

    i.update_ack_watermark(r2.seqnum)
    assert i.is_record_old(r1)
    assert i.is_record_old(r2)
    assert not i.is_record_old(r3)


def test_open_data_close(observe_errors):
    i, m, host_addr = make_inbound()
    scid1 = b"scid"
    scid2 = b"scXX"
    c = mock.Mock()
    i.use_connection(c)
    sc1 = mock.Mock()
    peer_addr = object()
    with mock.patch("wormhole._dilation.inbound.SubChannel",
                    side_effect=[sc1]) as sc:
        with mock.patch("wormhole._dilation.inbound.SubchannelAddress",
                        side_effect=[peer_addr]) as sca:
            i.handle_open(scid1, "proto")
    assert m._subprotocol_factories.mock_calls == [mock.call._got_open(sc1, peer_addr)]
    assert sc.mock_calls == [mock.call(scid1, m, host_addr, peer_addr)]
    assert sca.mock_calls == [mock.call("proto")]

    # reset calls
    m._subprotocol_factories.mock_calls[:] = []

    # a subsequent duplicate OPEN should be ignored
    with mock.patch("wormhole._dilation.inbound.SubChannel",
                    side_effect=[sc1]) as sc:
        with mock.patch("wormhole._dilation.inbound.SubchannelAddress",
                        side_effect=[peer_addr]) as sca:
            i.handle_open(scid1, "proto")
    assert m._subprotocol_factories.mock_calls == []
    assert sc.mock_calls == []
    assert sca.mock_calls == []
    observe_errors.flush(DuplicateOpenError)

    i.handle_data(scid1, b"data")
    assert sc1.mock_calls == [mock.call.remote_data(b"data")]
    sc1.mock_calls[:] = []

    i.handle_data(scid2, b"for non-existent subchannel")
    assert sc1.mock_calls == []
    observe_errors.flush(DataForMissingSubchannelError)

    i.handle_close(scid1)
    assert sc1.mock_calls == [mock.call.remote_close()]
    sc1.mock_calls[:] = []

    i.handle_close(scid2)
    assert sc1.mock_calls == []
    observe_errors.flush(CloseForMissingSubchannelError)

    # after the subchannel is closed, the Manager will notify Inbound
    i.subchannel_closed(scid1, sc1)

    i.stop_using_connection()


def test_pause():
    i, m, host_addr = make_inbound()
    c = mock.Mock()

    # add two subchannels, pause one, then add a connection
    scid1 = b"sci1"
    scid2 = b"sci2"
    sc1 = mock.Mock()
    sc2 = mock.Mock()
    peer_addr = object()
    with mock.patch("wormhole._dilation.inbound.SubChannel",
                    side_effect=[sc1, sc2]):
        with mock.patch("wormhole._dilation.inbound.SubchannelAddress",
                        return_value=peer_addr):
            i.handle_open(scid1, "proto")
            i.handle_open(scid2, "proto")
    assert c.mock_calls == []

    i.subchannel_pauseProducing(sc1)
    assert c.mock_calls == []
    i.subchannel_resumeProducing(sc1)
    assert c.mock_calls == []
    i.subchannel_pauseProducing(sc1)
    assert c.mock_calls == []

    i.use_connection(c)
    assert c.mock_calls == [mock.call.pauseProducing()]
    c.mock_calls[:] = []

    i.subchannel_resumeProducing(sc1)
    assert c.mock_calls == [mock.call.resumeProducing()]
    c.mock_calls[:] = []

    # consumers aren't really supposed to do this, but tolerate it
    i.subchannel_resumeProducing(sc1)
    assert c.mock_calls == []

    i.subchannel_pauseProducing(sc1)
    assert c.mock_calls == [mock.call.pauseProducing()]
    c.mock_calls[:] = []
    i.subchannel_pauseProducing(sc2)
    assert c.mock_calls == []  # was already paused

    # tolerate duplicate pauseProducing
    i.subchannel_pauseProducing(sc2)
    assert c.mock_calls == []

    # stopProducing is treated like a terminal resumeProducing
    i.subchannel_stopProducing(sc1)
    assert c.mock_calls == []
    i.subchannel_stopProducing(sc2)
    assert c.mock_calls == [mock.call.resumeProducing()]
    c.mock_calls[:] = []
