import re
from unittest import mock
from twisted.internet import reactor
from twisted.internet.task import Cooperator
from twisted.internet.defer import Deferred
from zope.interface import implementer

import pytest
from pytest_twisted import ensureDeferred

from ... import _interfaces
from ...eventual import EventualQueue
from ..._interfaces import ITerminator
from ..._dilation import manager
from ..._dilation._noise import NoiseConnection


@implementer(_interfaces.ISend)
class MySend:
    def __init__(self, side):
        self.rx_phase = 0
        self.side = side

    def send(self, phase, plaintext):
        # print("SEND[%s]" % self.side, phase, plaintext)
        self.peer.got(phase, plaintext)

    def got(self, phase, plaintext):
        d_mo = re.search(r'^dilate-(\d+)$', phase)
        p = int(d_mo.group(1))
        assert p == self.rx_phase
        self.rx_phase += 1
        self.dilator.received_dilate(plaintext)


@implementer(ITerminator)
class FakeTerminator:
    def __init__(self):
        self.d = Deferred()

    def stoppedD(self):
        self.d.callback(None)


@ensureDeferred
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test1():
    # print()
    send_left = MySend("left")
    send_right = MySend("right")
    send_left.peer = send_right
    send_right.peer = send_left
    key = b"\x00"*32
    eq = EventualQueue(reactor)
    cooperator = Cooperator(scheduler=eq.eventually)

    t_left = FakeTerminator()
    t_right = FakeTerminator()

    d_left = manager.Dilator(reactor, eq, cooperator, ["ged"])
    d_left.wire(send_left, t_left)
    d_left.got_key(key)
    d_left.got_wormhole_versions({"can-dilate": ["ged"]})
    send_left.dilator = d_left

    d_right = manager.Dilator(reactor, eq, cooperator, ["ged"])
    d_right.wire(send_right, t_right)
    d_right.got_key(key)
    d_right.got_wormhole_versions({"can-dilate": ["ged"]})
    send_right.dilator = d_right

    with mock.patch("wormhole._dilation.connector.ipaddrs.find_addresses",
                    return_value=["127.0.0.1"]):
        d_left.dilate(no_listen=True)
        d_right.dilate()

    # print("left connected", eps_left)
    # print("right connected", eps_right)

    # we normally shut down with w.close(), which calls Dilator.stop(),
    # which calls Terminator.stoppedD(), which (after everything else is
    # done) calls Boss.stopped
    d_left.stop()
    d_right.stop()

    await t_left.d
    await t_right.d
