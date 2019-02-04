import re
import mock
from twisted.internet import reactor
from twisted.trial import unittest
from twisted.internet.task import Cooperator
from twisted.internet.defer import inlineCallbacks
from zope.interface import implementer

from ... import _interfaces
from ...eventual import EventualQueue
from ..._dilation import manager


@implementer(_interfaces.ISend)
class MySend(object):
    def __init__(self, side):
        self.rx_phase = 0
        self.side = side
    def send(self, phase, plaintext):
        #print("SEND[%s]" % self.side, phase, plaintext)
        self.peer.got(phase, plaintext)
    def got(self, phase, plaintext):
        d_mo = re.search(r'^dilate-(\d+)$', phase)
        p = int(d_mo.group(1))
        assert p == self.rx_phase
        self.rx_phase += 1
        self.dilator.received_dilate(plaintext)

class Connect(unittest.TestCase):
    @inlineCallbacks
    def test1(self):
        #print()
        send_left = MySend("left")
        send_right = MySend("right")
        send_left.peer = send_right
        send_right.peer = send_left
        key = b"\x00"*32
        eq = EventualQueue(reactor)
        cooperator = Cooperator(scheduler=eq.eventually)

        d_left = manager.Dilator(reactor, eq, cooperator, no_listen=True)
        d_left.wire(send_left)
        d_left.got_key(key)
        d_left.got_wormhole_versions({"can-dilate": ["1"]})
        send_left.dilator = d_left

        d_right = manager.Dilator(reactor, eq, cooperator)
        d_right.wire(send_right)
        d_right.got_key(key)
        d_right.got_wormhole_versions({"can-dilate": ["1"]})
        send_right.dilator = d_right

        with mock.patch("wormhole._dilation.connector.ipaddrs.find_addresses",
                        return_value=["127.0.0.1"]):
            eps_left_d = d_left.dilate()
            eps_right_d = d_right.dilate()

        eps_left = yield eps_left_d
        eps_right = yield eps_right_d

        #print("left connected", eps_left)
        #print("right connected", eps_right)

        control_ep_left, connect_ep_left, listen_ep_left = eps_left
        control_ep_right, connect_ep_right, listen_ep_right = eps_right

        #control_ep_left.connect(

        # we shut down with w.close(), which calls Dilator.stop(), which
        # calls manager.stop()
        yield d_left.stop()
        yield d_right.stop()

