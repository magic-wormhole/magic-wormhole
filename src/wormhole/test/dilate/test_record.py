from __future__ import print_function, unicode_literals
import mock
from zope.interface import alsoProvides
from twisted.trial import unittest
from ..._dilation._noise import NoiseInvalidMessage
from ..._dilation.connection import (IFramer, Frame, Prologue,
                                     _Record, Handshake,
                                     Disconnect, Ping)
from ..._dilation.roles import LEADER


def make_record():
    f = mock.Mock()
    alsoProvides(f, IFramer)
    n = mock.Mock()  # pretends to be a Noise object
    r = _Record(f, n, LEADER)
    r.set_role_leader()
    return r, f, n


class Record(unittest.TestCase):
    def test_good2(self):
        f = mock.Mock()
        alsoProvides(f, IFramer)
        f.add_and_parse = mock.Mock(side_effect=[
            [],
            [Prologue()],
            [Frame(frame=b"rx-handshake")],
            [Frame(frame=b"frame1"), Frame(frame=b"frame2")],
        ])
        n = mock.Mock()
        n.write_message = mock.Mock(return_value=b"tx-handshake")
        p1, p2 = object(), object()
        n.decrypt = mock.Mock(side_effect=[p1, p2])
        r = _Record(f, n, LEADER)
        r.set_role_leader()
        self.assertEqual(f.mock_calls, [])
        r.connectionMade()
        self.assertEqual(f.mock_calls, [mock.call.connectionMade()])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [mock.call.start_handshake()])
        n.mock_calls[:] = []

        # Pretend to deliver the prologue in two parts. The text we send in
        # doesn't matter: the side_effect= is what causes the prologue to be
        # recognized by the second call.
        self.assertEqual(list(r.add_and_unframe(b"pro")), [])
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"pro")])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [])

        # recognizing the prologue causes a handshake frame to be sent
        self.assertEqual(list(r.add_and_unframe(b"logue")), [])
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"logue"),
                                        mock.call.send_frame(b"tx-handshake")])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [mock.call.write_message()])
        n.mock_calls[:] = []

        # next add_and_unframe is recognized as the Handshake
        self.assertEqual(list(r.add_and_unframe(b"blah")), [Handshake()])
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"blah")])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [mock.call.read_message(b"rx-handshake")])
        n.mock_calls[:] = []

        # next is a pair of Records
        r1, r2 = object(), object()
        with mock.patch("wormhole._dilation.connection.parse_record",
                        side_effect=[r1, r2]) as pr:
            self.assertEqual(list(r.add_and_unframe(b"blah2")), [r1, r2])
            self.assertEqual(n.mock_calls, [mock.call.decrypt(b"frame1"),
                                            mock.call.decrypt(b"frame2")])
            self.assertEqual(pr.mock_calls, [mock.call(p1), mock.call(p2)])

    def test_bad_handshake(self):
        f = mock.Mock()
        alsoProvides(f, IFramer)
        f.add_and_parse = mock.Mock(return_value=[Prologue(),
                                                  Frame(frame=b"rx-handshake")])
        n = mock.Mock()
        n.write_message = mock.Mock(return_value=b"tx-handshake")
        nvm = NoiseInvalidMessage()
        n.read_message = mock.Mock(side_effect=nvm)
        r = _Record(f, n, LEADER)
        r.set_role_leader()
        self.assertEqual(f.mock_calls, [])
        r.connectionMade()
        self.assertEqual(f.mock_calls, [mock.call.connectionMade()])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [mock.call.start_handshake()])
        n.mock_calls[:] = []

        with mock.patch("wormhole._dilation.connection.log.err") as le:
            with self.assertRaises(Disconnect):
                list(r.add_and_unframe(b"data"))
        self.assertEqual(le.mock_calls,
                         [mock.call(nvm, "bad inbound noise handshake")])

    def test_bad_message(self):
        f = mock.Mock()
        alsoProvides(f, IFramer)
        f.add_and_parse = mock.Mock(return_value=[Prologue(),
                                                  Frame(frame=b"rx-handshake"),
                                                  Frame(frame=b"bad-message")])
        n = mock.Mock()
        n.write_message = mock.Mock(return_value=b"tx-handshake")
        nvm = NoiseInvalidMessage()
        n.decrypt = mock.Mock(side_effect=nvm)
        r = _Record(f, n, LEADER)
        r.set_role_leader()
        self.assertEqual(f.mock_calls, [])
        r.connectionMade()
        self.assertEqual(f.mock_calls, [mock.call.connectionMade()])
        f.mock_calls[:] = []
        self.assertEqual(n.mock_calls, [mock.call.start_handshake()])
        n.mock_calls[:] = []

        with mock.patch("wormhole._dilation.connection.log.err") as le:
            with self.assertRaises(Disconnect):
                list(r.add_and_unframe(b"data"))
        self.assertEqual(le.mock_calls,
                         [mock.call(nvm, "bad inbound noise frame")])

    def test_send_record(self):
        f = mock.Mock()
        alsoProvides(f, IFramer)
        n = mock.Mock()
        f1 = object()
        n.encrypt = mock.Mock(return_value=f1)
        r1 = Ping(b"pingid")
        r = _Record(f, n, LEADER)
        r.set_role_leader()
        self.assertEqual(f.mock_calls, [])
        m1 = object()
        with mock.patch("wormhole._dilation.connection.encode_record",
                        return_value=m1) as er:
            r.send_record(r1)
        self.assertEqual(er.mock_calls, [mock.call(r1)])
        self.assertEqual(n.mock_calls, [mock.call.start_handshake(),
                                        mock.call.encrypt(m1)])
        self.assertEqual(f.mock_calls, [mock.call.send_frame(f1)])

    def test_good(self):
        # Exercise the success path. The Record instance is given each chunk
        # of data as it arrives on Protocol.dataReceived, and is supposed to
        # return a series of Tokens (maybe none, if the chunk was incomplete,
        # or more than one, if the chunk was larger). Internally, it delivers
        # the chunks to the Framer for unframing (which returns 0 or more
        # frames), manages the Noise decryption object, and parses any
        # decrypted messages into tokens (some of which are consumed
        # internally, others for delivery upstairs).
        #
        # in the normal flow, we get:
        #
        # |   | Inbound   | NoiseAction   | Outbound  | ToUpstairs |
        # |   | -         | -             | -         | -          |
        # | 1 |           |               | prologue  |            |
        # | 2 | prologue  |               |           |            |
        # | 3 |           | write_message | handshake |            |
        # | 4 | handshake | read_message  |           | Handshake  |
        # | 5 |           | encrypt       | KCM       |            |
        # | 6 | KCM       | decrypt       |           | KCM        |
        # | 7 | msg1      | decrypt       |           | msg1       |

        # 1: instantiating the Record instance causes the outbound prologue
        # to be sent

        # 2+3: receipt of the inbound prologue triggers creation of the
        # ephemeral key (the "handshake") by calling noise.write_message()
        # and then writes the handshake to the outbound transport

        # 4: when the peer's handshake is received, it is delivered to
        # noise.read_message(), which generates the shared key (enabling
        # noise.send() and noise.decrypt()). It also delivers the Handshake
        # token upstairs, which might (on the Follower) trigger immediate
        # transmission of the Key Confirmation Message (KCM)

        # 5: the outbound KCM is framed and fed into noise.encrypt(), then
        # sent outbound

        # 6: the peer's KCM is decrypted then delivered upstairs. The
        # Follower treats this as a signal that it should use this connection
        # (and drop all others).

        # 7: the peer's first message is decrypted, parsed, and delivered
        # upstairs. This might be an Open or a Data, depending upon what
        # queued messages were left over from the previous connection

        r, f, n = make_record()
        outbound_handshake = object()
        kcm, msg1 = object(), object()
        f_kcm, f_msg1 = object(), object()
        n.write_message = mock.Mock(return_value=outbound_handshake)
        n.decrypt = mock.Mock(side_effect=[kcm, msg1])
        n.encrypt = mock.Mock(side_effect=[f_kcm, f_msg1])
        f.add_and_parse = mock.Mock(side_effect=[[],  # no tokens yet
                                                 [Prologue()],
                                                 [Frame("f_handshake")],
                                                 [Frame("f_kcm"),
                                                  Frame("f_msg1")],
                                                 ])

        self.assertEqual(f.mock_calls, [])
        self.assertEqual(n.mock_calls, [mock.call.start_handshake()])
        n.mock_calls[:] = []

        # 1. The Framer is responsible for sending the prologue, so we don't
        # have to check that here, we just check that the Framer was told
        # about connectionMade properly.
        r.connectionMade()
        self.assertEqual(f.mock_calls, [mock.call.connectionMade()])
        self.assertEqual(n.mock_calls, [])
        f.mock_calls[:] = []

        # 2
        # we dribble the prologue in over two messages, to make sure we can
        # handle a dataReceived that doesn't complete the token

        # remember, add_and_unframe is a generator
        self.assertEqual(list(r.add_and_unframe(b"pro")), [])
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"pro")])
        self.assertEqual(n.mock_calls, [])
        f.mock_calls[:] = []

        self.assertEqual(list(r.add_and_unframe(b"logue")), [])
        # 3: write_message, send outbound handshake
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"logue"),
                                        mock.call.send_frame(outbound_handshake),
                                        ])
        self.assertEqual(n.mock_calls, [mock.call.write_message()])
        f.mock_calls[:] = []
        n.mock_calls[:] = []

        # 4
        # Now deliver the Noise "handshake", the ephemeral public key. This
        # is framed, but not a record, so it shouldn't decrypt or parse
        # anything, but the handshake is delivered to the Noise object, and
        # it does return a Handshake token so we can let the next layer up
        # react (by sending the KCM frame if we're a Follower, or not if
        # we're the Leader)

        self.assertEqual(list(r.add_and_unframe(b"handshake")), [Handshake()])
        self.assertEqual(f.mock_calls, [mock.call.add_and_parse(b"handshake")])
        self.assertEqual(n.mock_calls, [mock.call.read_message("f_handshake")])
        f.mock_calls[:] = []
        n.mock_calls[:] = []

        # 5: at this point we ought to be able to send a message, the KCM
        with mock.patch("wormhole._dilation.connection.encode_record",
                        side_effect=[b"r-kcm"]) as er:
            r.send_record(kcm)
        self.assertEqual(er.mock_calls, [mock.call(kcm)])
        self.assertEqual(n.mock_calls, [mock.call.encrypt(b"r-kcm")])
        self.assertEqual(f.mock_calls, [mock.call.send_frame(f_kcm)])
        n.mock_calls[:] = []
        f.mock_calls[:] = []

        # 6: Now we deliver two messages stacked up: the KCM (Key
        # Confirmation Message) and the first real message. Concatenating
        # them tests that we can handle more than one token in a single
        # chunk. We need to mock parse_record() because everything past the
        # handshake is decrypted and parsed.

        with mock.patch("wormhole._dilation.connection.parse_record",
                        side_effect=[kcm, msg1]) as pr:
            self.assertEqual(list(r.add_and_unframe(b"kcm,msg1")),
                             [kcm, msg1])
            self.assertEqual(f.mock_calls,
                             [mock.call.add_and_parse(b"kcm,msg1")])
            self.assertEqual(n.mock_calls, [mock.call.decrypt("f_kcm"),
                                            mock.call.decrypt("f_msg1")])
            self.assertEqual(pr.mock_calls, [mock.call(kcm), mock.call(msg1)])
        n.mock_calls[:] = []
        f.mock_calls[:] = []
