from twisted.internet import reactor
from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks
from attrs import evolve


from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection
from ..._status import Connecting, Connected, Disconnected, WormholeStatus, NoKey, AllegedSharedKey, ConfirmedKey, DilationStatus, NoPeer

from ..common import ServerBase


class API(ServerBase, unittest.TestCase):

    @inlineCallbacks
    def test_dilation_status(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")

        eq = EventualQueue(reactor)

        status0 = []
        status1 = []

        wormhole_status0 = []
        wormhole_status1 = []

        from wormhole.wormhole import create
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

        # we don't actually do anything, just disconnect
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
                WormholeStatus(Connecting(self.relayurl, 1), NoKey()),
                WormholeStatus(Connected(self.relayurl), NoKey()),
                WormholeStatus(Connected(self.relayurl), AllegedSharedKey()),
                WormholeStatus(Connected(self.relayurl), ConfirmedKey()),
                WormholeStatus(Disconnected(), NoKey()),
            ]
        )

        # check that the Dilation status messages are correct
        for s in status0:
            print(s)

        self.assertEqual(
            status0,
            [
                DilationStatus(WormholeStatus(Connected(self.relayurl), AllegedSharedKey()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), AllegedSharedKey()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Connected(self.relayurl), ConfirmedKey()), 0, NoPeer()),
                DilationStatus(WormholeStatus(Disconnected(), NoKey()), 0, NoPeer()),
            ]
        )
