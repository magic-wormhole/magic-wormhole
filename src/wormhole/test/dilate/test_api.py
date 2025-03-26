from twisted.internet import reactor
from twisted.trial import unittest
from twisted.internet.task import deferLater
from attrs import evolve

import pytest
import pytest_twisted

from ...wormhole import create
from ...errors import LonelyError
from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection
from ..._status import Connecting, Connected, Disconnected, WormholeStatus, NoKey, AllegedSharedKey, ConfirmedKey, DilationStatus, NoPeer, ConnectedPeer, ConnectingPeer

from ..common import ServerBase


@pytest_twisted.ensureDeferred()
async def test_on_status_error(reactor, mailbox):
    """
    Our user code raises an exception during status processing
    """
    eq = EventualQueue(reactor)

    class FakeError(Exception):
        pass

    def on_status(_):
        raise FakeError()
    with pytest.raises(FakeError):
        w = create(
            "appid", mailbox.url,
            reactor,
            versions={"fun": "quux"},
            _eventual_queue=eq,
            _enable_dilate=True,
            on_status_update=on_status,
        )
        await w.allocate_code()
        code = await w.get_code()
        print(code)
        try:
            await w.close()
        except LonelyError:
            pass

@pytest_twisted.ensureDeferred()
async def test_dilation_status(reactor, mailbox):
    if not NoiseConnection:
        raise unittest.SkipTest("noiseprotocol unavailable")

    eq = EventualQueue(reactor)

    status0 = []
    status1 = []

    wormhole_status0 = []
    wormhole_status1 = []

    w0 = create(
        "appid", mailbox.url,
        reactor,
        versions={"fun": "quux"},
        _eventual_queue=eq,
        _enable_dilate=True,
        on_status_update=wormhole_status0.append,
    )

    w1 = create(
        "appid", mailbox.url,
        reactor,
        versions={"bar": "baz"},
        _eventual_queue=eq,
        _enable_dilate=True,
        on_status_update=wormhole_status1.append,
    )

    w0.allocate_code()
    code = await w0.get_code()

    w1.set_code(code)

    w0.dilate(on_status_update=status0.append)
    w1.dilate(on_status_update=status1.append)

    # we should see the _other side's_ app-versions
    v0 = await w1.get_versions()
    v1 = await w0.get_versions()
    assert v0 == {"fun": "quux"}
    assert v1 == {"bar": "baz"}

    @pytest_twisted.ensureDeferred()
    async def wait_for_peer():
        while True:
            await deferLater(reactor, 0.001, lambda: None)
            peers = [
                st
                for st in status0
                if isinstance(st.peer_connection, ConnectedPeer)
            ]
            if peers:
                return
    await wait_for_peer()

    # we don't actually do anything, just disconnect after we have
    # our peer
    await w0.close()
    await w1.close()

    # check that the wormhole status messages are what we expect
    def normalize_timestamp(status):
        if isinstance(status.mailbox_connection, Connecting):
            return evolve(
                status,
                mailbox_connection=evolve(status.mailbox_connection, last_attempt=1),
            )
        return status

    processed = [
        normalize_timestamp(status)
        for status in wormhole_status0
    ]

    assert processed == [
        WormholeStatus(Connecting(mailbox.url, 1), NoKey()),
        WormholeStatus(Connected(mailbox.url), NoKey()),
        WormholeStatus(Connected(mailbox.url), AllegedSharedKey()),
        WormholeStatus(Connected(mailbox.url), ConfirmedKey()),
        WormholeStatus(Disconnected(), NoKey()),
    ]

    # we are "normalizing" all the timestamps to be "0" because we
    # are using the real reactor and therefore it is difficult to
    # predict what they'll be. Removing the "real reactor" is
    # itself kind of a deep problem due to the "eventually()"
    # usage (among some other reasons).

    def normalize_peer(st):
        typ = type(st.peer_connection)
        peer = st.peer_connection
        if typ == ConnectingPeer:
            peer = evolve(peer, last_attempt=0)
        elif typ == ConnectedPeer:
            peer = evolve(peer, connected_at=0, expires_at=0, hint_description="hint")
        return evolve(st, peer_connection=peer)

    normalized = [normalize_peer(st) for st in status0]

    # for n in normalized: print(n)

    # check that the Dilation status messages are correct
    assert normalized == [
        DilationStatus(WormholeStatus(Connected(mailbox.url), AllegedSharedKey()), 0, NoPeer()),
        DilationStatus(WormholeStatus(Connected(mailbox.url), AllegedSharedKey()), 0, NoPeer()),
        DilationStatus(WormholeStatus(Connected(mailbox.url), ConfirmedKey()), 0, NoPeer()),
        DilationStatus(WormholeStatus(Connected(mailbox.url), ConfirmedKey()), 0, ConnectingPeer(0)),
        DilationStatus(WormholeStatus(Connected(mailbox.url), ConfirmedKey()), 0, ConnectedPeer(0, 0, hint_description="hint")),
        DilationStatus(WormholeStatus(Disconnected(), NoKey()), 0, ConnectedPeer(0, 0, hint_description="hint")),
        DilationStatus(WormholeStatus(Disconnected(), NoKey()), 0, NoPeer()),
    ]
