from zope.interface import implementer
from automat import MethodicalMachine
from . import _interfaces

@implementer(_interfaces.IOrder)
class Order(object):
    m = MethodicalMachine()
    def __init__(self, side, timing):
        self._side = side
        self._timing = timing
        self._key = None
        self._queue = []
    def wire(self, key, receive):
        self._K = _interfaces.IKey(key)
        self._R = _interfaces.IReceive(receive)

    @m.state(initial=True)
    def S0_no_pake(self): pass
    @m.state(terminal=True)
    def S1_yes_pake(self): pass

    def got_message(self, phase, payload):
        if phase == "pake":
            self.got_pake(phase, payload)
        else:
            self.got_non_pake(phase, payload)

    @m.input()
    def got_pake(self, phase, payload): pass
    @m.input()
    def got_non_pake(self, phase, payload): pass

    @m.output()
    def queue(self, phase, payload):
        self._queue.append((phase, payload))
    @m.output()
    def notify_key(self, phase, payload):
        self._K.got_pake(payload)
    @m.output()
    def drain(self, phase, payload):
        del phase
        del payload
        for (phase, payload) in self._queue:
            self._deliver(phase, payload)
        self._queue[:] = []
    @m.output()
    def deliver(self, phase, payload):
        self._deliver(phase, payload)

    def _deliver(self, phase, payload):
        self._R.got_message(phase, payload)

    S0_no_pake.upon(got_non_pake, enter=S0_no_pake, outputs=[queue])
    S0_no_pake.upon(got_pake, enter=S1_yes_pake, outputs=[notify_key, drain])
    S1_yes_pake.upon(got_non_pake, enter=S1_yes_pake, outputs=[deliver])
