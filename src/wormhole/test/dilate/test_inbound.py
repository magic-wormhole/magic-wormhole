from __future__ import print_function, unicode_literals
from unittest import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from ..._interfaces import IDilationManager
from ..._dilation.connection import Open, Data, Close
from ..._dilation.inbound import (Inbound, DuplicateOpenError,
                                  DataForMissingSubchannelError,
                                  CloseForMissingSubchannelError)


def make_inbound():
    m = mock.Mock()
    alsoProvides(m, IDilationManager)
    host_addr = object()
    i = Inbound(m, host_addr)
    return i, m, host_addr


class InboundTest(unittest.TestCase):
    def test_seqnum(self):
        i, m, host_addr = make_inbound()
        r1 = Open(scid=513, seqnum=1)
        r2 = Data(scid=513, seqnum=2, data=b"")
        r3 = Close(scid=513, seqnum=3)
        self.assertFalse(i.is_record_old(r1))
        self.assertFalse(i.is_record_old(r2))
        self.assertFalse(i.is_record_old(r3))

        i.update_ack_watermark(r1.seqnum)
        self.assertTrue(i.is_record_old(r1))
        self.assertFalse(i.is_record_old(r2))
        self.assertFalse(i.is_record_old(r3))

        i.update_ack_watermark(r2.seqnum)
        self.assertTrue(i.is_record_old(r1))
        self.assertTrue(i.is_record_old(r2))
        self.assertFalse(i.is_record_old(r3))

    def test_open_data_close(self):
        i, m, host_addr = make_inbound()
        scid1 = b"scid"
        scid2 = b"scXX"
        c = mock.Mock()
        lep = mock.Mock()
        i.set_listener_endpoint(lep)
        i.use_connection(c)
        sc1 = mock.Mock()
        peer_addr = object()
        with mock.patch("wormhole._dilation.inbound.SubChannel",
                        side_effect=[sc1]) as sc:
            with mock.patch("wormhole._dilation.inbound._SubchannelAddress",
                            side_effect=[peer_addr]) as sca:
                i.handle_open(scid1)
        self.assertEqual(lep.mock_calls, [mock.call._got_open(sc1, peer_addr)])
        self.assertEqual(sc.mock_calls, [mock.call(scid1, m, host_addr, peer_addr)])
        self.assertEqual(sca.mock_calls, [mock.call(scid1)])
        lep.mock_calls[:] = []

        # a subsequent duplicate OPEN should be ignored
        with mock.patch("wormhole._dilation.inbound.SubChannel",
                        side_effect=[sc1]) as sc:
            with mock.patch("wormhole._dilation.inbound._SubchannelAddress",
                            side_effect=[peer_addr]) as sca:
                i.handle_open(scid1)
        self.assertEqual(lep.mock_calls, [])
        self.assertEqual(sc.mock_calls, [])
        self.assertEqual(sca.mock_calls, [])
        self.flushLoggedErrors(DuplicateOpenError)

        i.handle_data(scid1, b"data")
        self.assertEqual(sc1.mock_calls, [mock.call.remote_data(b"data")])
        sc1.mock_calls[:] = []

        i.handle_data(scid2, b"for non-existent subchannel")
        self.assertEqual(sc1.mock_calls, [])
        self.flushLoggedErrors(DataForMissingSubchannelError)

        i.handle_close(scid1)
        self.assertEqual(sc1.mock_calls, [mock.call.remote_close()])
        sc1.mock_calls[:] = []

        i.handle_close(scid2)
        self.assertEqual(sc1.mock_calls, [])
        self.flushLoggedErrors(CloseForMissingSubchannelError)

        # after the subchannel is closed, the Manager will notify Inbound
        i.subchannel_closed(scid1, sc1)

        i.stop_using_connection()

    def test_control_channel(self):
        i, m, host_addr = make_inbound()
        lep = mock.Mock()
        i.set_listener_endpoint(lep)

        scid0 = b"scid"
        sc0 = mock.Mock()
        i.set_subchannel_zero(scid0, sc0)

        # OPEN on the control channel identifier should be ignored as a
        # duplicate, since the control channel is already registered
        sc1 = mock.Mock()
        peer_addr = object()
        with mock.patch("wormhole._dilation.inbound.SubChannel",
                        side_effect=[sc1]) as sc:
            with mock.patch("wormhole._dilation.inbound._SubchannelAddress",
                            side_effect=[peer_addr]) as sca:
                i.handle_open(scid0)
        self.assertEqual(lep.mock_calls, [])
        self.assertEqual(sc.mock_calls, [])
        self.assertEqual(sca.mock_calls, [])
        self.flushLoggedErrors(DuplicateOpenError)

        # and DATA to it should be delivered correctly
        i.handle_data(scid0, b"data")
        self.assertEqual(sc0.mock_calls, [mock.call.remote_data(b"data")])
        sc0.mock_calls[:] = []

    def test_pause(self):
        i, m, host_addr = make_inbound()
        c = mock.Mock()
        lep = mock.Mock()
        i.set_listener_endpoint(lep)

        # add two subchannels, pause one, then add a connection
        scid1 = b"sci1"
        scid2 = b"sci2"
        sc1 = mock.Mock()
        sc2 = mock.Mock()
        peer_addr = object()
        with mock.patch("wormhole._dilation.inbound.SubChannel",
                        side_effect=[sc1, sc2]):
            with mock.patch("wormhole._dilation.inbound._SubchannelAddress",
                            return_value=peer_addr):
                i.handle_open(scid1)
                i.handle_open(scid2)
        self.assertEqual(c.mock_calls, [])

        i.subchannel_pauseProducing(sc1)
        self.assertEqual(c.mock_calls, [])
        i.subchannel_resumeProducing(sc1)
        self.assertEqual(c.mock_calls, [])
        i.subchannel_pauseProducing(sc1)
        self.assertEqual(c.mock_calls, [])

        i.use_connection(c)
        self.assertEqual(c.mock_calls, [mock.call.pauseProducing()])
        c.mock_calls[:] = []

        i.subchannel_resumeProducing(sc1)
        self.assertEqual(c.mock_calls, [mock.call.resumeProducing()])
        c.mock_calls[:] = []

        # consumers aren't really supposed to do this, but tolerate it
        i.subchannel_resumeProducing(sc1)
        self.assertEqual(c.mock_calls, [])

        i.subchannel_pauseProducing(sc1)
        self.assertEqual(c.mock_calls, [mock.call.pauseProducing()])
        c.mock_calls[:] = []
        i.subchannel_pauseProducing(sc2)
        self.assertEqual(c.mock_calls, [])  # was already paused

        # tolerate duplicate pauseProducing
        i.subchannel_pauseProducing(sc2)
        self.assertEqual(c.mock_calls, [])

        # stopProducing is treated like a terminal resumeProducing
        i.subchannel_stopProducing(sc1)
        self.assertEqual(c.mock_calls, [])
        i.subchannel_stopProducing(sc2)
        self.assertEqual(c.mock_calls, [mock.call.resumeProducing()])
        c.mock_calls[:] = []
