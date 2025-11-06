from attr import attrib, attrs
from attr.validators import instance_of
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces
from ._key import derive_phase_key, encrypt_data
from .util import provides


@attrs
@implementer(_interfaces.ISend)
class Send:
    _side = attrib(validator=instance_of(str))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._queue = []

    def wire(self, mailbox):
        self._M = _interfaces.IMailbox(mailbox)

    @m.state(initial=True)
    def S0_no_key(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S1_verified_key(self):
        pass  # pragma: no cover

    # from Receive
    @m.input()
    def got_verified_key(self, key):
        pass

    # from Boss
    @m.input()
    def send(self, phase, plaintext):
        pass

    @m.output()
    def queue(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        self._queue.append((phase, plaintext))

    @m.output()
    def record_key(self, key):
        self._key = key

    @m.output()
    def drain(self, key):
        del key
        for (phase, plaintext) in self._queue:
            self._encrypt_and_send(phase, plaintext)
        self._queue[:] = []

    @m.output()
    def deliver(self, phase, plaintext):
        assert isinstance(phase, str), type(phase)
        assert isinstance(plaintext, bytes), type(plaintext)
        self._encrypt_and_send(phase, plaintext)

    def _encrypt_and_send(self, phase, plaintext):
        assert self._key
        data_key = derive_phase_key(self._key, self._side, phase)
        encrypted = encrypt_data(data_key, plaintext)
        self._M.add_message(phase, encrypted)

    S0_no_key.upon(send, enter=S0_no_key, outputs=[queue])
    S0_no_key.upon(
        got_verified_key, enter=S1_verified_key, outputs=[record_key, drain])
    S1_verified_key.upon(send, enter=S1_verified_key, outputs=[deliver])
