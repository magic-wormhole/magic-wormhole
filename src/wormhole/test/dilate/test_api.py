from twisted.internet import reactor
from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks
from twisted.internet.task import deferLater
from attrs import evolve


from ...wormhole import create
from ...errors import LonelyError
from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection
from ..._status import Connecting, Connected, Disconnected, WormholeStatus, NoKey, AllegedSharedKey, ConfirmedKey, DilationStatus, NoPeer, ConnectedPeer, ConnectingPeer, NoCode, AllocatedCode, ConsumedCode

from ..common import ServerBase


class API(ServerBase, unittest.TestCase):

    @inlineCallbacks
    def test_on_status_error(self):
        """
        Our user code raises an exception during status processing
        """
        eq = EventualQueue(reactor)

        class FakeError(Exception):
            pass

        def on_status(_):
            raise FakeError()
        with self.assertRaises(FakeError):
            w = create(
                "appid", self.relayurl,
                reactor,
                versions={"fun": "quux"},
                _eventual_queue=eq,
                _enable_dilate=True,
                on_status_update=on_status,
            )
            yield w.allocate_code()
            code = yield w.get_code()
            print(code)
            try:
                yield w.close()
            except LonelyError:
                pass

    @inlineCallbacks
    def test_dilation_status(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")

        eq = EventualQueue(reactor)

        status0 = []
        status1 = []

        wormhole_status0 = []
        wormhole_status1 = []

        w0 = create(
            "appid", self.relayurl,
            reactor,
            versions={"fun": "quux"},
            _eventual_queue=eq,
            _enable_dilate=True,
            on_status_update=wormhole_status0.append,
        )

        w1 = create(
            "appid", self.relayurl,
            reactor,
            versions={"bar": "baz"},
            _eventual_queue=eq,
            _enable_dilate=True,
            on_status_update=wormhole_status1.append,
        )

        yield w0.allocate_code()
        code = yield w0.get_code()

        yield w1.set_code(code)

        yield w0.dilate(on_status_update=status0.append)
        yield w1.dilate(on_status_update=status1.append)

        # we should see the _other side's_ app-versions
        v0 = yield w1.get_versions()
        v1 = yield w0.get_versions()
        self.assertEqual(v0, {"fun": "quux"})
        self.assertEqual(v1, {"bar": "baz"})

        @inlineCallbacks
        def wait_for_peer():
            while True:
                yield deferLater(reactor, 0.001, lambda: None)
                peers = [
                    st
                    for st in status0
                    if isinstance(st.peer_connection, ConnectedPeer)
                ]
                if peers:
                    return
        yield wait_for_peer()

        # we don't actually do anything, just disconnect after we have
        # our peer
        yield w0.close()
        yield w1.close()

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

        self.assertEqual(
            processed,
            [
                WormholeStatus(Disconnected(), NoKey(), NoCode()),
                WormholeStatus(Connecting(self.relayurl, 1), NoKey(), NoCode()),
                WormholeStatus(Connected(self.relayurl), NoKey(), NoCode()),
                WormholeStatus(Connected(self.relayurl), NoKey(), AllocatedCode()),
                # XXX order of this
                WormholeStatus(Connected(self.relayurl), AllegedSharedKey(), AllocatedCode()),
                # XXX ...and this fluctuates
                WormholeStatus(Connected(self.relayurl), AllegedSharedKey(), ConsumedCode()),
                WormholeStatus(Connected(self.relayurl), ConfirmedKey(), ConsumedCode()),
            ]
        )

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

        for n in normalized: print(n)

        # check that the Dilation status messages are correct
        self.assertEqual(
            normalized,
            [
                DilationStatus(WormholeStatus(Connected(self.relayurl), AllegedSharedKey(), AllocatedCode()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), AllegedSharedKey(), ConsumedCode()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), AllegedSharedKey(), ConsumedCode()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), ConfirmedKey(), ConsumedCode()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), ConfirmedKey(), ConsumedCode()), 0, ConnectingPeer(0)),
                DilationStatus(WormholeStatus(Connected(self.relayurl), ConfirmedKey(), ConsumedCode()), 0, ConnectedPeer(0, 0, hint_description="hint")),
                DilationStatus(WormholeStatus(Disconnected(), ConfirmedKey(), ConsumedCode()), 0, ConnectedPeer(0, 0, hint_description="hint")),
                DilationStatus(WormholeStatus(Disconnected(), ConfirmedKey(), ConsumedCode()), 0, NoPeer()),
            ]
        )
