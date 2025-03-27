from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.interfaces import ITransport
from ..._dilation._noise import NoiseInvalidMessage, NoiseConnection
from ..._dilation.connection import (IFramer, Frame, Prologue,
                                     _Record, Handshake, KCM,
                                     Disconnect, Ping, _Framer, Data)
from ..._dilation.connector import build_noise
from ..._dilation.roles import LEADER, FOLLOWER
from zope.interface import implementer
import pytest


def make_record():
    f = mock.Mock()
    alsoProvides(f, IFramer)
    n = mock.Mock()  # pretends to be a Noise object
    r = _Record(f, n, LEADER)
    r.set_role_leader()
    return r, f, n


def test_good2():
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
    assert f.mock_calls == []
    r.connectionMade()
    assert f.mock_calls == [mock.call.connectionMade()]
    f.mock_calls[:] = []
    assert n.mock_calls == [mock.call.start_handshake()]
    n.mock_calls[:] = []

    # Pretend to deliver the prologue in two parts. The text we send in
    # doesn't matter: the side_effect= is what causes the prologue to be
    # recognized by the second call.
    assert list(r.add_and_unframe(b"pro")) == []
    assert f.mock_calls == [mock.call.add_and_parse(b"pro")]
    f.mock_calls[:] = []
    assert n.mock_calls == []

    # recognizing the prologue causes a handshake frame to be sent
    assert list(r.add_and_unframe(b"logue")) == []
    assert f.mock_calls == [mock.call.add_and_parse(b"logue"),
                                    mock.call.send_frame(b"tx-handshake")]
    f.mock_calls[:] = []
    assert n.mock_calls == [mock.call.write_message()]
    n.mock_calls[:] = []

    # next add_and_unframe is recognized as the Handshake
    assert list(r.add_and_unframe(b"blah")) == [Handshake()]
    assert f.mock_calls == [mock.call.add_and_parse(b"blah")]
    f.mock_calls[:] = []
    assert n.mock_calls == [mock.call.read_message(b"rx-handshake")]
    n.mock_calls[:] = []

    # next is a pair of Records
    r1, r2 = object(), object()
    with mock.patch("wormhole._dilation.connection.parse_record",
                    side_effect=[r1, r2]) as pr:
        assert list(r.add_and_unframe(b"blah2")) == [r1, r2]
        assert n.mock_calls == [mock.call.decrypt(b"frame1"),
                                        mock.call.decrypt(b"frame2")]
        assert pr.mock_calls == [mock.call(p1), mock.call(p2)]

def test_bad_handshake():
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
    assert f.mock_calls == []
    r.connectionMade()
    assert f.mock_calls == [mock.call.connectionMade()]
    f.mock_calls[:] = []
    assert n.mock_calls == [mock.call.start_handshake()]
    n.mock_calls[:] = []

    with mock.patch("wormhole._dilation.connection.log.err") as le:
        with pytest.raises(Disconnect):
            list(r.add_and_unframe(b"data"))
    assert le.mock_calls == \
                     [mock.call(nvm, "bad inbound noise handshake")]

def test_bad_message():
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
    assert f.mock_calls == []
    r.connectionMade()
    assert f.mock_calls == [mock.call.connectionMade()]
    f.mock_calls[:] = []
    assert n.mock_calls == [mock.call.start_handshake()]
    n.mock_calls[:] = []

    with mock.patch("wormhole._dilation.connection.log.err") as le:
        with pytest.raises(Disconnect):
            list(r.add_and_unframe(b"data"))
    assert le.mock_calls == \
                     [mock.call(nvm, "bad inbound noise frame")]

def test_send_record():
    f = mock.Mock()
    alsoProvides(f, IFramer)
    n = mock.Mock()
    f1 = b"some bytes"
    n.encrypt = mock.Mock(return_value=f1)
    r1 = Ping(b"pingid")
    r = _Record(f, n, LEADER)
    r.set_role_leader()
    assert f.mock_calls == []
    m1 = b"some other bytes"
    with mock.patch("wormhole._dilation.connection.encode_record",
                    return_value=m1) as er:
        r.send_record(r1)
    assert er.mock_calls == [mock.call(r1)]
    assert n.mock_calls == [mock.call.start_handshake(),
                                    mock.call.encrypt(m1)]
    assert f.mock_calls == [mock.call.send_frame(f1)]

def test_good():
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

    assert f.mock_calls == []
    assert n.mock_calls == [mock.call.start_handshake()]
    n.mock_calls[:] = []

    # 1. The Framer is responsible for sending the prologue, so we don't
    # have to check that here, we just check that the Framer was told
    # about connectionMade properly.
    r.connectionMade()
    assert f.mock_calls == [mock.call.connectionMade()]
    assert n.mock_calls == []
    f.mock_calls[:] = []

    # 2
    # we dribble the prologue in over two messages, to make sure we can
    # handle a dataReceived that doesn't complete the token

    # remember, add_and_unframe is a generator
    assert list(r.add_and_unframe(b"pro")) == []
    assert f.mock_calls == [mock.call.add_and_parse(b"pro")]
    assert n.mock_calls == []
    f.mock_calls[:] = []

    assert list(r.add_and_unframe(b"logue")) == []
    # 3: write_message, send outbound handshake
    assert f.mock_calls == [mock.call.add_and_parse(b"logue"),
                                    mock.call.send_frame(outbound_handshake),
                                    ]
    assert n.mock_calls == [mock.call.write_message()]
    f.mock_calls[:] = []
    n.mock_calls[:] = []

    # 4
    # Now deliver the Noise "handshake", the ephemeral public key. This
    # is framed, but not a record, so it shouldn't decrypt or parse
    # anything, but the handshake is delivered to the Noise object, and
    # it does return a Handshake token so we can let the next layer up
    # react (by sending the KCM frame if we're a Follower, or not if
    # we're the Leader)

    assert list(r.add_and_unframe(b"handshake")) == [Handshake()]
    assert f.mock_calls == [mock.call.add_and_parse(b"handshake")]
    assert n.mock_calls == [mock.call.read_message("f_handshake")]
    f.mock_calls[:] = []
    n.mock_calls[:] = []

    # 5: at this point we ought to be able to send a message, the KCM
    with mock.patch("wormhole._dilation.connection.encode_record",
                    side_effect=[b"r-kcm"]) as er:
        r.send_record(kcm)
    assert er.mock_calls == [mock.call(kcm)]
    assert n.mock_calls == [mock.call.encrypt(b"r-kcm")]
    assert f.mock_calls == [mock.call.send_frame(f_kcm)]
    n.mock_calls[:] = []
    f.mock_calls[:] = []

    # 6: Now we deliver two messages stacked up: the KCM (Key
    # Confirmation Message) and the first real message. Concatenating
    # them tests that we can handle more than one token in a single
    # chunk. We need to mock parse_record() because everything past the
    # handshake is decrypted and parsed.

    with mock.patch("wormhole._dilation.connection.parse_record",
                    side_effect=[kcm, msg1]) as pr:
        assert list(r.add_and_unframe(b"kcm,msg1")) == \
                         [kcm, msg1]
        assert f.mock_calls == \
                         [mock.call.add_and_parse(b"kcm,msg1")]
        assert n.mock_calls == [mock.call.decrypt("f_kcm"),
                                        mock.call.decrypt("f_msg1")]
        assert pr.mock_calls == [mock.call(kcm), mock.call(msg1)]
    n.mock_calls[:] = []
    f.mock_calls[:] = []

def test_large_frame():
    """
    Noise only allows 64KiB message, but the API allows up to 4GiB
    frames
    """
    if not NoiseConnection:
        import unittest
        raise unittest.SkipTest("noiseprotocol unavailable")
    # XXX could really benefit from some Hypothesis style
    # exploration of more cases .. but we don't already depend on
    # that library, so a future improvement

    @implementer(ITransport)
    class FakeTransport:
        """
        Record which write()s happen
        """
        def __init__(self):
            self.data = []  # list of bytes

        def write(self, data):
            self.data.append(data)

    # we build both sides of a connection so that underlying Noise
    # structures can be set up and paired properly. Essentially
    # this test is acting like the L2 Protocol object, and can
    # feed bytes to / from either side
    pake_secret = b"\x00" * 32
    transport0 = FakeTransport()
    transport1 = FakeTransport()
    noise0 = build_noise()
    noise0.set_psks(pake_secret)
    noise0.set_as_initiator()  # leader
    noise1 = build_noise()
    noise1.set_psks(pake_secret)
    noise1.set_as_responder()  # follower
    framer0 = _Framer(transport0, b"out prolog", b"in prolog")
    record0 = _Record(framer0, noise0, LEADER)
    record0.set_role_leader()
    record0.connectionMade()
    assert list(record0.add_and_unframe(b"in prolog")) == \
        []

    # note that in connector the prologues flip around depending
    # on who is the leader or follower
    framer1 = _Framer(transport1, b"in prolog", b"out prolog")
    record1 = _Record(framer1, noise1, FOLLOWER)
    record1.set_role_follower()
    record1.connectionMade()

    # the leader has now sent the prolog and the opening handshake
    # message, so we consume them on the follower side

    assert list(record1.add_and_unframe(transport0.data[0])) == [] # b"out prolog"
    assert list(record1.add_and_unframe(transport0.data[1])) == [Handshake()]

    # the follower sends the first KCM; the leader is waiting for
    # this and will complete the handshake after that
    record1.send_record(KCM())
    assert list(record0.add_and_unframe(transport1.data[1])) == \
        [Handshake()]
    record0.send_record(KCM())
    assert list(record1.add_and_unframe(transport0.data[2])) == \
        [KCM()]
    # both sides are now done their handshakes

    # Now, we send a message that's definitely bigger than a
    # single Noise message can deal with
    input_plaintext = b"\xff" * 65537
    record0.send_record(
        Data(
            seqnum=123,
            scid=456,
            data=input_plaintext,
        )
    )

    # ...despite its size the message should still be sent out as
    # a single Data, because _our_ framing handles 4-byte lengths
    assert len(transport0.data[3]) == \
        4 + 65537 + (16 * 2) + 1 + 4 + 4
    #   ^-- frame-length
    #       ^-- plaintext size
    #               ^-- Noise associated data, x2 noise packets
    #                          ^-- "data" kind, 0x04
    #                              ^-- subchannel-id
    #                                  ^-- sequence number
    #
    # (maybe we don't need the above assert, since if any of that
    # isn't true the next step will fail)

    # round-trip the data to the other side and ensure we get the
    # plaintext back
    outputs = list(
        record1.add_and_unframe(transport0.data[3])
    )
    assert len(outputs) == 1
    assert outputs[0] == \
        Data(
            seqnum=123,
            scid=456,
            data=input_plaintext,
        )
