from __future__ import print_function, unicode_literals
import mock
from twisted.trial import unittest
from twisted.internet.interfaces import ITransport
from twisted.internet.error import ConnectionDone
from ..._dilation.subchannel import (Once, SubChannel,
                                     _WormholeAddress, _SubchannelAddress,
                                     AlreadyClosedError)
from .common import mock_manager


def make_sc(set_protocol=True):
    scid = 4
    hostaddr = _WormholeAddress()
    peeraddr = _SubchannelAddress(scid)
    m = mock_manager()
    sc = SubChannel(scid, m, hostaddr, peeraddr)
    p = mock.Mock()
    if set_protocol:
        sc._set_protocol(p)
    return sc, m, scid, hostaddr, peeraddr, p


class SubChannelAPI(unittest.TestCase):
    def test_once(self):
        o = Once(ValueError)
        o()
        with self.assertRaises(ValueError):
            o()

    def test_create(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()
        self.assert_(ITransport.providedBy(sc))
        self.assertEqual(m.mock_calls, [])
        self.assertIdentical(sc.getHost(), hostaddr)
        self.assertIdentical(sc.getPeer(), peeraddr)

    def test_write(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        sc.write(b"data")
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"data")])
        m.mock_calls[:] = []
        sc.writeSequence([b"more", b"data"])
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"moredata")])

    def test_write_when_closing(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        sc.loseConnection()
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid)])
        m.mock_calls[:] = []

        with self.assertRaises(AlreadyClosedError) as e:
            sc.write(b"data")
        self.assertEqual(str(e.exception),
                         "write not allowed on closed subchannel")

    def test_local_close(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        sc.loseConnection()
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid)])
        m.mock_calls[:] = []

        # late arriving data is still delivered
        sc.remote_data(b"late")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"late")])
        p.mock_calls[:] = []

        sc.remote_close()
        self.assert_connectionDone(p.mock_calls)

    def test_local_double_close(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        sc.loseConnection()
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid)])
        m.mock_calls[:] = []

        with self.assertRaises(AlreadyClosedError) as e:
            sc.loseConnection()
        self.assertEqual(str(e.exception),
                         "loseConnection not allowed on closed subchannel")

    def assert_connectionDone(self, mock_calls):
        self.assertEqual(len(mock_calls), 1)
        self.assertEqual(mock_calls[0][0], "connectionLost")
        self.assertEqual(len(mock_calls[0][1]), 1)
        self.assertIsInstance(mock_calls[0][1][0], ConnectionDone)

    def test_remote_close(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()
        sc.remote_close()
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid),
                                        mock.call.subchannel_closed(scid, sc)])
        self.assert_connectionDone(p.mock_calls)

    def test_data(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()
        sc.remote_data(b"data")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"data")])
        p.mock_calls[:] = []
        sc.remote_data(b"not")
        sc.remote_data(b"coalesced")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"not"),
                                        mock.call.dataReceived(b"coalesced"),
                                        ])

    def test_data_before_open(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc(set_protocol=False)
        sc.remote_data(b"data")
        self.assertEqual(p.mock_calls, [])
        sc._set_protocol(p)
        sc._deliver_queued_data()
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"data")])
        p.mock_calls[:] = []
        sc.remote_data(b"more")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"more")])

    def test_close_before_open(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc(set_protocol=False)
        sc.remote_close()
        self.assertEqual(p.mock_calls, [])
        sc._set_protocol(p)
        sc._deliver_queued_data()
        self.assert_connectionDone(p.mock_calls)

    def test_producer(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        sc.pauseProducing()
        self.assertEqual(m.mock_calls, [mock.call.subchannel_pauseProducing(sc)])
        m.mock_calls[:] = []
        sc.resumeProducing()
        self.assertEqual(m.mock_calls, [mock.call.subchannel_resumeProducing(sc)])
        m.mock_calls[:] = []
        sc.stopProducing()
        self.assertEqual(m.mock_calls, [mock.call.subchannel_stopProducing(sc)])
        m.mock_calls[:] = []

    def test_consumer(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc()

        # TODO: more, once this is implemented
        sc.registerProducer(None, True)
        sc.unregisterProducer()
