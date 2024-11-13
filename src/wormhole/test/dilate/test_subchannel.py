from unittest import mock
from zope.interface import directlyProvides
from twisted.trial import unittest
from twisted.internet.interfaces import ITransport, IHalfCloseableProtocol
from twisted.internet.error import ConnectionDone
from ..._dilation.subchannel import (Once, SubChannel,
                                     _WormholeAddress, _SubchannelAddress,
                                     AlreadyClosedError,
                                     NormalCloseUsedOnHalfCloseable)
from .common import mock_manager


def make_sc(set_protocol=True, half_closeable=False):
    scid = 4
    hostaddr = _WormholeAddress()
    peeraddr = _SubchannelAddress(scid)
    m = mock_manager()
    sc = SubChannel(scid, m, hostaddr, peeraddr)
    p = mock.Mock()
    if half_closeable:
        directlyProvides(p, IHalfCloseableProtocol)
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
        sc.remote_data(b"data1")
        sc.remote_data(b"data2")
        self.assertEqual(p.mock_calls, [])
        sc._set_protocol(p)
        sc._deliver_queued_data()
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"data1"),
                                        mock.call.dataReceived(b"data2")])
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


class HalfCloseable(unittest.TestCase):

    def test_create(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)
        self.assert_(ITransport.providedBy(sc))
        self.assertEqual(m.mock_calls, [])
        self.assertIdentical(sc.getHost(), hostaddr)
        self.assertIdentical(sc.getPeer(), peeraddr)

    def test_local_close(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)

        sc.write(b"data")
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"data")])
        m.mock_calls[:] = []
        sc.writeSequence([b"more", b"data"])
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"moredata")])
        m.mock_calls[:] = []

        sc.remote_data(b"inbound1")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"inbound1")])
        p.mock_calls[:] = []

        with self.assertRaises(NormalCloseUsedOnHalfCloseable) as e:
            sc.loseConnection()  # TODO: maybe this shouldn't be an error

        # after a local close, we can't write anymore, but we can still
        # receive data
        sc.loseWriteConnection()  # TODO or loseConnection?
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid)])
        m.mock_calls[:] = []
        self.assertEqual(p.mock_calls, [mock.call.writeConnectionLost()])
        p.mock_calls[:] = []

        with self.assertRaises(AlreadyClosedError) as e:
            sc.write(b"data")
        self.assertEqual(str(e.exception),
                         "write not allowed on closed subchannel")

        with self.assertRaises(AlreadyClosedError) as e:
            sc.loseWriteConnection()
        self.assertEqual(str(e.exception),
                         "loseConnection not allowed on closed subchannel")

        with self.assertRaises(NormalCloseUsedOnHalfCloseable) as e:
            sc.loseConnection()  # TODO: maybe expect AlreadyClosedError

        sc.remote_data(b"inbound2")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"inbound2")])
        p.mock_calls[:] = []

        # the remote end will finally shut down the connection
        sc.remote_close()
        self.assertEqual(m.mock_calls, [mock.call.subchannel_closed(scid, sc)])
        self.assertEqual(p.mock_calls, [mock.call.readConnectionLost()])

    def test_remote_close(self):
        sc, m, scid, hostaddr, peeraddr, p = make_sc(half_closeable=True)

        sc.write(b"data")
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"data")])
        m.mock_calls[:] = []

        sc.remote_data(b"inbound1")
        self.assertEqual(p.mock_calls, [mock.call.dataReceived(b"inbound1")])
        p.mock_calls[:] = []

        # after a remote close, we can still write data
        sc.remote_close()
        self.assertEqual(m.mock_calls, [])
        self.assertEqual(p.mock_calls, [mock.call.readConnectionLost()])
        p.mock_calls[:] = []

        sc.write(b"out2")
        self.assertEqual(m.mock_calls, [mock.call.send_data(scid, b"out2")])
        m.mock_calls[:] = []

        # and a local close will shutdown the connection
        sc.loseWriteConnection()
        self.assertEqual(m.mock_calls, [mock.call.send_close(scid),
                                        mock.call.subchannel_closed(scid, sc)])
        self.assertEqual(p.mock_calls, [mock.call.writeConnectionLost()])
