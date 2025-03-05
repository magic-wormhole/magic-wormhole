import re
from unittest import mock
from twisted.internet import reactor
from twisted.trial import unittest
from twisted.internet.task import Cooperator
from twisted.internet.defer import Deferred, inlineCallbacks
from zope.interface import implementer

from ... import _interfaces
from ...eventual import EventualQueue
from ..._interfaces import ITerminator
from ..._dilation import manager
from ..._dilation._noise import NoiseConnection

from ..common import ServerBase


class API(ServerBase, unittest.TestCase):

    @inlineCallbacks
    def test_dilation_status(self):
        if not NoiseConnection:
            raise unittest.SkipTest("noiseprotocol unavailable")

        eq = EventualQueue(reactor)
        cooperator = Cooperator(scheduler=eq.eventually)

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
            versions={"fun": "quux"},
            _eventual_queue=eq,
            _enable_dilate=True,
            on_status_update=wormhole_status1.append,
        )

        yield w0.allocate_code()
        code = yield w0.get_code()

        yield w1.set_code(code)

        endpoints0 = yield w0.dilate(on_status_update=status0.append)
        endpoints1 = yield w1.dilate(on_status_update=status1.append)

        from twisted.internet.task import deferLater
        yield deferLater(reactor, 2.0, lambda: None)

        yield w0.close()
        yield w1.close()

        print("STATUS")
        for st in status0:
            print(st)
        print("WORMHOLE STATUS")
        for st in wormhole_status0:
            print(st)

