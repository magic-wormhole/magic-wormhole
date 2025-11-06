from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.task import Clock
from twisted.internet.interfaces import ITransport
import pytest_twisted

from ...eventual import EventualQueue
from ..._interfaces import IDilationConnector
from ..._dilation.roles import LEADER, FOLLOWER
from ..._dilation.connection import (DilatedConnectionProtocol, encode_record,
                                     KCM, Open, Ack)
from .common import clear_mock_calls


def make_con(role, use_relay=False):
    clock = Clock()
    eq = EventualQueue(clock)
    connector = mock.Mock()
    alsoProvides(connector, IDilationConnector)
    n = mock.Mock()  # pretends to be a Noise object
    n.write_message = mock.Mock(side_effect=[b"handshake"])
    c = DilatedConnectionProtocol(eq, role, "desc", connector, n,
                                  b"outbound_prologue\n", b"inbound_prologue\n")
    if use_relay:
        c.use_relay(b"relay_handshake\n")
    t = mock.Mock()
    alsoProvides(t, ITransport)
    return c, n, connector, t, eq


def test_hashable():
    c, n, connector, t, eq = make_con(LEADER)
    hash(c)

@pytest_twisted.ensureDeferred
async def test_bad_prologue():
    c, n, connector, t, eq = make_con(LEADER)
    c.makeConnection(t)
    d = c.when_disconnected()
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    clear_mock_calls(n, connector, t)

    c.dataReceived(b"prologue\n")
    assert n.mock_calls == []
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.loseConnection()]

    eq.flush_sync()
    assert not d.called
    c.connectionLost(b"why")
    eq.flush_sync()
    conn = await d
    assert conn is c

def _test_no_relay(role):
    c, n, connector, t, eq = make_con(role)
    t_kcm = KCM()
    t_open = Open(seqnum=1, scid=0x11223344, subprotocol="proto")
    t_ack = Ack(resp_seqnum=2)
    n.decrypt = mock.Mock(side_effect=[
        encode_record(t_kcm),
        encode_record(t_open),
    ])
    exp_kcm = b"\x00\x00\x00\x03kcm"
    n.encrypt = mock.Mock(side_effect=[b"kcm", b"ack1"])
    m = mock.Mock()  # Manager

    c.makeConnection(t)
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"inbound_prologue\n")

    exp_handshake = b"\x00\x00\x00\x09handshake"
    if role is LEADER:
        # the LEADER sends the Noise handshake message immediately upon
        # receipt of the prologue
        assert n.mock_calls == [mock.call.write_message()]
        assert t.mock_calls == [mock.call.write(exp_handshake)]
    else:
        # however the FOLLOWER waits until receiving the leader's
        # handshake before sending their own
        assert n.mock_calls == []
        assert t.mock_calls == []
    assert connector.mock_calls == []

    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"\x00\x00\x00\x0Ahandshake2")
    if role is LEADER:
        # we're the leader, so we don't send the KCM right away
        assert n.mock_calls == [
            mock.call.read_message(b"handshake2")]
        assert connector.mock_calls == []
        assert t.mock_calls == []
        assert c._manager is None
    else:
        # we're the follower, so we send our Noise handshake, then
        # encrypt and send the KCM immediately
        assert n.mock_calls == [
            mock.call.read_message(b"handshake2"),
            mock.call.write_message(),
            mock.call.encrypt(encode_record(t_kcm)),
        ]
        assert connector.mock_calls == []
        assert t.mock_calls == [
            mock.call.write(exp_handshake),
            mock.call.write(exp_kcm)]
        assert c._manager is None
    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"\x00\x00\x00\x03KCM")
    # leader: inbound KCM means we add the candidate
    # follower: inbound KCM means we've been selected.
    # in both cases we notify Connector.add_candidate(), and the Connector
    # decides if/when to call .select()

    assert n.mock_calls == [mock.call.decrypt(b"KCM")]
    assert connector.mock_calls == [mock.call.add_candidate(c)]
    assert t.mock_calls == []
    clear_mock_calls(n, connector, t, m)

    # now pretend this connection wins (either the Leader decides to use
    # this one among all the candidates, or we're the Follower and the
    # Connector is reacting to add_candidate() by recognizing we're the
    # only candidate there is)
    c.select(m)
    assert c._manager is m
    if role is LEADER:
        # TODO: currently Connector.select_and_stop_remaining() is
        # responsible for sending the KCM just before calling c.select()
        # iff we're the LEADER, therefore Connection.select won't send
        # anything. This should be moved to c.select().
        assert n.mock_calls == []
        assert connector.mock_calls == []
        assert t.mock_calls == []
        # there's a call to .have_peer(...) but we don't really care?
        assert len(m.mock_calls) == 1##[])

        c.send_record(KCM())
        assert n.mock_calls == [
            mock.call.encrypt(encode_record(t_kcm)),
        ]
        assert connector.mock_calls == []
        assert t.mock_calls == [mock.call.write(exp_kcm)]
        assert len(m.mock_calls) == 1  ##[]) .have_peer() call
    else:
        # follower: we already sent the KCM, do nothing
        assert n.mock_calls == []
        assert connector.mock_calls == []
        assert t.mock_calls == []
        assert len(m.mock_calls) == 1  # have_peer()
    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"\x00\x00\x00\x04msg1")
    assert n.mock_calls == [mock.call.decrypt(b"msg1")]
    assert connector.mock_calls == []
    assert t.mock_calls == []
    assert m.mock_calls == [mock.call.got_record(t_open)]
    clear_mock_calls(n, connector, t, m)

    c.send_record(t_ack)
    exp_ack = b"\x06\x00\x00\x00\x02"
    assert n.mock_calls == [mock.call.encrypt(exp_ack)]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"\x00\x00\x00\x04ack1")]
    assert m.mock_calls == []
    clear_mock_calls(n, connector, t, m)

    c.disconnect()
    assert n.mock_calls == []
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.loseConnection()]
    assert m.mock_calls == []
    clear_mock_calls(n, connector, t, m)

def test_no_relay_leader():
    return _test_no_relay(LEADER)

def test_no_relay_follower():
    return _test_no_relay(FOLLOWER)

def test_relay():
    c, n, connector, t, eq = make_con(LEADER, use_relay=True)

    c.makeConnection(t)
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"relay_handshake\n")]
    clear_mock_calls(n, connector, t)

    c.dataReceived(b"ok\n")
    assert n.mock_calls == []
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    clear_mock_calls(n, connector, t)

    c.dataReceived(b"inbound_prologue\n")
    assert n.mock_calls == [mock.call.write_message()]
    assert connector.mock_calls == []
    exp_handshake = b"\x00\x00\x00\x09handshake"
    assert t.mock_calls == [mock.call.write(exp_handshake)]
    clear_mock_calls(n, connector, t)


@pytest_twisted.ensureDeferred
async def test_relay_jilted():
    c, n, connector, t, eq = make_con(LEADER, use_relay=True)
    d = c.when_disconnected()

    c.makeConnection(t)
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"relay_handshake\n")]
    clear_mock_calls(n, connector, t)

    c.connectionLost(b"why")
    eq.flush_sync()
    conn = await d
    assert conn is c

def test_relay_bad_response():
    c, n, connector, t, eq = make_con(LEADER, use_relay=True)

    c.makeConnection(t)
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"relay_handshake\n")]
    clear_mock_calls(n, connector, t)

    c.dataReceived(b"not ok\n")
    assert n.mock_calls == []
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.loseConnection()]
    clear_mock_calls(n, connector, t)

def test_follower_combined():
    c, n, connector, t, eq = make_con(FOLLOWER)
    t_kcm = KCM()
    t_open = Open(seqnum=1, scid=0x11223344, subprotocol="proto")
    n.decrypt = mock.Mock(side_effect=[
        encode_record(t_kcm),
        encode_record(t_open),
    ])
    exp_kcm = b"\x00\x00\x00\x03kcm"
    n.encrypt = mock.Mock(side_effect=[b"kcm", b"ack1"])
    m = mock.Mock()  # Manager

    c.makeConnection(t)
    assert n.mock_calls == [mock.call.start_handshake()]
    assert connector.mock_calls == []
    assert t.mock_calls == [mock.call.write(b"outbound_prologue\n")]
    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"inbound_prologue\n")

    exp_handshake = b"\x00\x00\x00\x09handshake"
    # however the FOLLOWER waits until receiving the leader's
    # handshake before sending their own
    assert n.mock_calls == []
    assert t.mock_calls == []
    assert connector.mock_calls == []

    clear_mock_calls(n, connector, t, m)

    c.dataReceived(b"\x00\x00\x00\x0Ahandshake2")
    # we're the follower, so we send our Noise handshake, then
    # encrypt and send the KCM immediately
    assert n.mock_calls == [
        mock.call.read_message(b"handshake2"),
        mock.call.write_message(),
        mock.call.encrypt(encode_record(t_kcm)),
    ]
    assert connector.mock_calls == []
    assert t.mock_calls == [
        mock.call.write(exp_handshake),
        mock.call.write(exp_kcm)]
    assert c._manager is None
    clear_mock_calls(n, connector, t, m)

    # the leader will select a connection, send the KCM, and then
    # immediately send some more data

    kcm_and_msg1 = (b"\x00\x00\x00\x03KCM" +
                    b"\x00\x00\x00\x04msg1")
    c.dataReceived(kcm_and_msg1)

    # follower: inbound KCM means we've been selected.
    # in both cases we notify Connector.add_candidate(), and the Connector
    # decides if/when to call .select()

    assert n.mock_calls == [mock.call.decrypt(b"KCM"),
                                    mock.call.decrypt(b"msg1")]
    assert connector.mock_calls == [mock.call.add_candidate(c)]
    assert t.mock_calls == []
    clear_mock_calls(n, connector, t, m)

    # now pretend this connection wins (either the Leader decides to use
    # this one among all the candidates, or we're the Follower and the
    # Connector is reacting to add_candidate() by recognizing we're the
    # only candidate there is)
    c.select(m)
    assert c._manager is m
    # follower: we already sent the KCM, do nothing
    assert n.mock_calls == []
    assert connector.mock_calls == []
    assert t.mock_calls == []
    assert m.mock_calls[1:] == [mock.call.got_record(t_open)]
    clear_mock_calls(n, connector, t, m)
