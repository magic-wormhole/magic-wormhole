from __future__ import print_function, unicode_literals
from unittest import mock
from twisted.trial import unittest
from ..._dilation.connection import (parse_record, encode_record,
                                     KCM, Ping, Pong, Open, Data, Close, Ack)


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_record(b"\x00"), KCM())
        self.assertEqual(parse_record(b"\x01\x55\x44\x33\x22"),
                         Ping(ping_id=b"\x55\x44\x33\x22"))
        self.assertEqual(parse_record(b"\x02\x55\x44\x33\x22"),
                         Pong(ping_id=b"\x55\x44\x33\x22"))
        self.assertEqual(parse_record(b"\x03\x00\x00\x02\x01\x00\x00\x01\x00"),
                         Open(scid=513, seqnum=256))
        self.assertEqual(parse_record(b"\x04\x00\x00\x02\x02\x00\x00\x01\x01dataaa"),
                         Data(scid=514, seqnum=257, data=b"dataaa"))
        self.assertEqual(parse_record(b"\x05\x00\x00\x02\x03\x00\x00\x01\x02"),
                         Close(scid=515, seqnum=258))
        self.assertEqual(parse_record(b"\x06\x00\x00\x01\x03"),
                         Ack(resp_seqnum=259))
        with mock.patch("wormhole._dilation.connection.log.err") as le:
            with self.assertRaises(ValueError):
                parse_record(b"\x07unknown")
        self.assertEqual(le.mock_calls,
                         [mock.call("received unknown message type: {}".format(
                             b"\x07unknown"))])

    def test_encode(self):
        self.assertEqual(encode_record(KCM()), b"\x00")
        self.assertEqual(encode_record(Ping(ping_id=b"ping")), b"\x01ping")
        self.assertEqual(encode_record(Pong(ping_id=b"pong")), b"\x02pong")
        self.assertEqual(encode_record(Open(scid=65536, seqnum=16)),
                         b"\x03\x00\x01\x00\x00\x00\x00\x00\x10")
        self.assertEqual(encode_record(Data(scid=65537, seqnum=17, data=b"dataaa")),
                         b"\x04\x00\x01\x00\x01\x00\x00\x00\x11dataaa")
        self.assertEqual(encode_record(Close(scid=65538, seqnum=18)),
                         b"\x05\x00\x01\x00\x02\x00\x00\x00\x12")
        self.assertEqual(encode_record(Ack(resp_seqnum=19)),
                         b"\x06\x00\x00\x00\x13")
        with self.assertRaises(TypeError) as ar:
            encode_record("not a record")
        self.assertEqual(str(ar.exception), "not a record")
