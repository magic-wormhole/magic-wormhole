from __future__ import absolute_import, print_function, unicode_literals

from attr import attrib, attrs
from attr.validators import instance_of, provides
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces


@attrs
@implementer(_interfaces.IOrder)
class Order(object):
    _side = attrib(validator=instance_of(type(u"")))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._key = None
        self._queue = []

    def wire(self, key, receive):
        self._K = _interfaces.IKey(key)
        self._R = _interfaces.IReceive(receive)

    @m.state(initial=True)
    def S0_no_pake(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S1_yes_pake(self):
        pass  # pragma: no cover

    def got_message(self, side, phase, body):
        # print("ORDER[%s].got_message(%s)" % (self._side, phase))
        assert isinstance(side, type("")), type(phase)
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        if phase == "pake":
            self.got_pake(side, phase, body)
        else:
            self.got_non_pake(side, phase, body)

    @m.input()
    def got_pake(self, side, phase, body):
        pass

    @m.input()
    def got_non_pake(self, side, phase, body):
        pass

    @m.output()
    def queue(self, side, phase, body):
        assert isinstance(side, type("")), type(phase)
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        self._queue.append((side, phase, body))

    @m.output()
    def notify_key(self, side, phase, body):
        self._K.got_pake(body)

    @m.output()
    def drain(self, side, phase, body):
        del phase
        del body
        for (side, phase, body) in self._queue:
            self._deliver(side, phase, body)
        self._queue[:] = []

    @m.output()
    def deliver(self, side, phase, body):
        self._deliver(side, phase, body)

    def _deliver(self, side, phase, body):
        self._R.got_message(side, phase, body)

    S0_no_pake.upon(got_non_pake, enter=S0_no_pake, outputs=[queue])
    S0_no_pake.upon(got_pake, enter=S1_yes_pake, outputs=[notify_key, drain])
    S1_yes_pake.upon(got_non_pake, enter=S1_yes_pake, outputs=[deliver])
