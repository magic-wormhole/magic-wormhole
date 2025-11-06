import typing

import pytest
import pytest_twisted
from zope.interface import implementer
from twisted.internet.task import deferLater
from twisted.internet.protocol import Factory
from twisted.internet.interfaces import IProtocolFactory

from ... import create
from ...errors import LonelyError
from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection
from ..._status import (
    Disconnected, ConnectedPeer, Closed, ConnectionStatus, PeerSharedKey,
    CodeStatus, PeerConnection
)


def _union_sort_order(union_klass):
    """
    Convert a Union type into a dict mapping its members to a number
    representing their order in the original type
    """
    # note: we want these to be in the same order as in the _status
    # Union-type declarations
    return {
        klass: idx
        for idx, klass in enumerate(typing.get_args(union_klass))
    }


def assert_mailbox_status_order(status_messages):
    """
    Confirm the given status messages have the correct properties.

    Here we want to assert that individual status items "go the right
    way": that is, for most of these things we know that you can't go
    back to "AllegedSharedKey" after seeing a "ConfirmedKey" (for
    example).

    So what we do is make a sort-order for each status field, sort the
    messages by that, and assert that this sorted order is the same as
    the raw message order.
    """
    code_sorting = _union_sort_order(CodeStatus)
    key_sorting = _union_sort_order(PeerSharedKey)
    mailbox_sorting = _union_sort_order(ConnectionStatus)

    code_messages = [st.code for st in status_messages]
    acceptable_order = sorted(code_messages, key=lambda code: code_sorting[type(code)])
    assert acceptable_order == code_messages, "'code' status came in an illegal order"

    key_messages = [st.peer_key for st in status_messages]
    assert sorted(key_messages, key=lambda k: key_sorting[type(k)]) == key_messages

    # "in general" we can go from Connected back to Connecting,
    # but for this particular test we don't actually do that
    mailbox_messages = [st.mailbox_connection for st in status_messages]
    assert sorted(mailbox_messages, key=lambda k: mailbox_sorting[type(k)]) == mailbox_messages
    # initial and terminal status must be correct
    assert isinstance(mailbox_messages[0], Disconnected)
    assert isinstance(mailbox_messages[-1], Closed)


def assert_dilation_status_order(status_messages):
    """
    for "Dilation"-specific messages, we only analyze the non-wormhole
    status parts and use a similar trick to the above.
    """
    generations = [msg.generation for msg in status_messages]
    # generations must always increase ("sorted" is a stable-sort .. right??)
    assert sorted(generations) == generations, "generation number went backwards"

    # "In general" the peer_connection can go ConnectedPeer back to
    # ConnectingPeer multiple times, but we don't actually do that in
    # this particular test.
    #
    # shapr points out we could fix this by setting ConnectedPeer and
    # ReconnectingPeer to the same number -- then they should
    # stable-sort to the right thing (however, we do not yet have a
    # test that actually re-connects a peer). e.g.:
    #     peer_sorting[ReconnectingPeer] = peer_sorting[ConnectedPeer]
    peer_sorting = _union_sort_order(PeerConnection)
    peers = [msg.peer_connection for msg in status_messages]
    assert sorted(peers, key=lambda k: peer_sorting[type(k)]) == peers, "peer status went backwards"


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
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
            dilation=True,
            on_status_update=on_status,
        )
        await w.allocate_code()
        code = await w.get_code()
        print(code)
        try:
            await w.close()
        except LonelyError:
            pass


@implementer(IProtocolFactory)
class SubFac(Factory):
    subprotocol = "jemison"


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_dilation_status(reactor, mailbox):
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
        dilation=True,
        on_status_update=wormhole_status0.append,
    )

    w1 = create(
        "appid", mailbox.url,
        reactor,
        versions={"bar": "baz"},
        _eventual_queue=eq,
        dilation=True,
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

    # analyze the message orders

    assert_mailbox_status_order(wormhole_status0)
    assert_mailbox_status_order(wormhole_status1)

    assert_dilation_status_order(status0)
    assert_dilation_status_order(status1)

    # todo: we could expand these to timestamps: we know they
    # should never go backwards
