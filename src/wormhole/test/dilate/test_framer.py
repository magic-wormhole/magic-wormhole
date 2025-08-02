from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.interfaces import ITransport
from ..._dilation.connection import _Framer, Frame, Prologue, Disconnect
import pytest


def make_framer():
    t = mock.Mock()
    alsoProvides(t, ITransport)
    f = _Framer(t, b"outbound_prologue\n", b"inbound_prologue\n")
    return f, t


def test_bad_prologue_length():
    f, t = make_framer()
    assert t.mock_calls == []

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    t.mock_calls[:] = []
    assert [] == list(f.add_and_parse(b"inbound_"))  # wait for it
    assert t.mock_calls == []

    with mock.patch("wormhole._dilation.connection.log.msg") as m:
        with pytest.raises(Disconnect):
            list(f.add_and_parse(b"not the prologue after all"))
    assert m.mock_calls == \
                     [mock.call(f"bad prologue: {b'inbound_not the p'}")]
    assert t.mock_calls == []


def test_bad_prologue_newline():
    f, t = make_framer()
    assert t.mock_calls == []

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    t.mock_calls[:] = []
    assert [] == list(f.add_and_parse(b"inbound_"))  # wait for it

    assert [] == list(f.add_and_parse(b"not"))
    with mock.patch("wormhole._dilation.connection.log.msg") as m:
        with pytest.raises(Disconnect):
            list(f.add_and_parse(b"\n"))
    assert m.mock_calls == \
                     [mock.call("bad prologue: {}".format(
                         b"inbound_not\n"))]
    assert t.mock_calls == []


def test_good_prologue():
    f, t = make_framer()
    assert t.mock_calls == []

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    t.mock_calls[:] = []
    assert [Prologue()] == \
                     list(f.add_and_parse(b"inbound_prologue\n"))
    assert t.mock_calls == []

    # now send_frame should work
    f.send_frame(b"frame")
    assert t.mock_calls == \
                     [mock.call.write(b"\x00\x00\x00\x05frame")]


def test_bad_relay():
    f, t = make_framer()
    assert t.mock_calls == []
    f.use_relay(b"relay handshake\n")

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"relay handshake\n")]
    t.mock_calls[:] = []
    with mock.patch("wormhole._dilation.connection.log.msg") as m:
        with pytest.raises(Disconnect):
            list(f.add_and_parse(b"goodbye\n"))
    assert m.mock_calls == \
                     [mock.call(f"bad relay_ok: {b'goo'}")]
    assert t.mock_calls == []


def test_good_relay():
    f, t = make_framer()
    assert t.mock_calls == []
    f.use_relay(b"relay handshake\n")
    assert t.mock_calls == []

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"relay handshake\n")]
    t.mock_calls[:] = []

    assert [] == list(f.add_and_parse(b"ok\n"))
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]


def test_frame():
    f, t = make_framer()
    assert t.mock_calls == []

    f.connectionMade()
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    t.mock_calls[:] = []
    assert [Prologue()] == \
                     list(f.add_and_parse(b"inbound_prologue\n"))
    assert t.mock_calls == []

    encoded_frame = b"\x00\x00\x00\x05frame"
    assert [] == list(f.add_and_parse(encoded_frame[:2]))
    assert [] == list(f.add_and_parse(encoded_frame[2:6]))
    assert [Frame(frame=b"frame")] == \
                     list(f.add_and_parse(encoded_frame[6:]))
