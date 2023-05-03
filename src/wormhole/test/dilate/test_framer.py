from __future__ import print_function, unicode_literals
from unittest import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from twisted.internet.interfaces import ITransport
from ..._dilation.connection import _Framer, Frame, Prologue, Disconnect


def make_framer():
    t = mock.Mock()
    alsoProvides(t, ITransport)
    f = _Framer(t, b"outbound_prologue\n", b"inbound_prologue\n")
    return f, t


class Framer(unittest.TestCase):
    def test_bad_prologue_length(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"outbound_prologue\n")])
        t.mock_calls[:] = []
        self.assertEqual([], list(f.add_and_parse(b"inbound_")))  # wait for it
        self.assertEqual(t.mock_calls, [])

        with mock.patch("wormhole._dilation.connection.log.msg") as m:
            with self.assertRaises(Disconnect):
                list(f.add_and_parse(b"not the prologue after all"))
        self.assertEqual(m.mock_calls,
                         [mock.call("bad prologue: {}".format(
                             b"inbound_not the p"))])
        self.assertEqual(t.mock_calls, [])

    def test_bad_prologue_newline(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"outbound_prologue\n")])
        t.mock_calls[:] = []
        self.assertEqual([], list(f.add_and_parse(b"inbound_")))  # wait for it

        self.assertEqual([], list(f.add_and_parse(b"not")))
        with mock.patch("wormhole._dilation.connection.log.msg") as m:
            with self.assertRaises(Disconnect):
                list(f.add_and_parse(b"\n"))
        self.assertEqual(m.mock_calls,
                         [mock.call("bad prologue: {}".format(
                             b"inbound_not\n"))])
        self.assertEqual(t.mock_calls, [])

    def test_good_prologue(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"outbound_prologue\n")])
        t.mock_calls[:] = []
        self.assertEqual([Prologue()],
                         list(f.add_and_parse(b"inbound_prologue\n")))
        self.assertEqual(t.mock_calls, [])

        # now send_frame should work
        f.send_frame(b"frame")
        self.assertEqual(t.mock_calls,
                         [mock.call.write(b"\x00\x00\x00\x05frame")])

    def test_bad_relay(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])
        f.use_relay(b"relay handshake\n")

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"relay handshake\n")])
        t.mock_calls[:] = []
        with mock.patch("wormhole._dilation.connection.log.msg") as m:
            with self.assertRaises(Disconnect):
                list(f.add_and_parse(b"goodbye\n"))
        self.assertEqual(m.mock_calls,
                         [mock.call("bad relay_ok: {}".format(b"goo"))])
        self.assertEqual(t.mock_calls, [])

    def test_good_relay(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])
        f.use_relay(b"relay handshake\n")
        self.assertEqual(t.mock_calls, [])

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"relay handshake\n")])
        t.mock_calls[:] = []

        self.assertEqual([], list(f.add_and_parse(b"ok\n")))
        self.assertEqual(t.mock_calls, [mock.call.write(b"outbound_prologue\n")])

    def test_frame(self):
        f, t = make_framer()
        self.assertEqual(t.mock_calls, [])

        f.connectionMade()
        self.assertEqual(t.mock_calls, [mock.call.write(b"outbound_prologue\n")])
        t.mock_calls[:] = []
        self.assertEqual([Prologue()],
                         list(f.add_and_parse(b"inbound_prologue\n")))
        self.assertEqual(t.mock_calls, [])

        encoded_frame = b"\x00\x00\x00\x05frame"
        self.assertEqual([], list(f.add_and_parse(encoded_frame[:2])))
        self.assertEqual([], list(f.add_and_parse(encoded_frame[2:6])))
        self.assertEqual([Frame(frame=b"frame")],
                         list(f.add_and_parse(encoded_frame[6:])))
