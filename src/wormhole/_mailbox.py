from __future__ import absolute_import, print_function, unicode_literals

from attr import attrib, attrs
from attr.validators import instance_of
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces


@attrs
@implementer(_interfaces.IMailbox)
class Mailbox(object):
    _side = attrib(validator=instance_of(type(u"")))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._mailbox = None
        self._pending_outbound = {}
        self._processed = set()

    def wire(self, nameplate, rendezvous_connector, ordering, terminator):
        self._N = _interfaces.INameplate(nameplate)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._O = _interfaces.IOrder(ordering)
        self._T = _interfaces.ITerminator(terminator)

    # all -A states: not connected
    # all -B states: yes connected
    # B states serialize as A, so they deserialize as unconnected

    # S0: know nothing
    @m.state(initial=True)
    def S0A(self):
        pass  # pragma: no cover

    @m.state()
    def S0B(self):
        pass  # pragma: no cover

    # S1: mailbox known, not opened
    @m.state()
    def S1A(self):
        pass  # pragma: no cover

    # S2: mailbox known, opened
    # We've definitely tried to open the mailbox at least once, but it must
    # be re-opened with each connection, because open() is also subscribe()
    @m.state()
    def S2A(self):
        pass  # pragma: no cover

    @m.state()
    def S2B(self):
        pass  # pragma: no cover

    # S3: closing
    @m.state()
    def S3A(self):
        pass  # pragma: no cover

    @m.state()
    def S3B(self):
        pass  # pragma: no cover

    # S4: closed. We no longer care whether we're connected or not
    # @m.state()
    # def S4A(self): pass
    # @m.state()
    # def S4B(self): pass
    @m.state(terminal=True)
    def S4(self):
        pass  # pragma: no cover

    S4A = S4
    S4B = S4

    # from Terminator
    @m.input()
    def close(self, mood):
        pass

    # from Nameplate
    @m.input()
    def got_mailbox(self, mailbox):
        pass

    # from RendezvousConnector
    @m.input()
    def connected(self):
        pass

    @m.input()
    def lost(self):
        pass

    def rx_message(self, side, phase, body):
        assert isinstance(side, type("")), type(side)
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        if side == self._side:
            self.rx_message_ours(phase, body)
        else:
            self.rx_message_theirs(side, phase, body)

    @m.input()
    def rx_message_ours(self, phase, body):
        pass

    @m.input()
    def rx_message_theirs(self, side, phase, body):
        pass

    @m.input()
    def rx_closed(self):
        pass

    # from Send or Key
    @m.input()
    def add_message(self, phase, body):
        pass

    @m.output()
    def record_mailbox(self, mailbox):
        self._mailbox = mailbox

    @m.output()
    def RC_tx_open(self):
        assert self._mailbox
        self._RC.tx_open(self._mailbox)

    @m.output()
    def queue(self, phase, body):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), (type(body), phase, body)
        self._pending_outbound[phase] = body

    @m.output()
    def record_mailbox_and_RC_tx_open_and_drain(self, mailbox):
        self._mailbox = mailbox
        self._RC.tx_open(mailbox)
        self._drain()

    @m.output()
    def drain(self):
        self._drain()

    def _drain(self):
        for phase, body in self._pending_outbound.items():
            self._RC.tx_add(phase, body)

    @m.output()
    def RC_tx_add(self, phase, body):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        self._RC.tx_add(phase, body)

    @m.output()
    def N_release_and_accept(self, side, phase, body):
        self._N.release()
        if phase not in self._processed:
            self._processed.add(phase)
            self._O.got_message(side, phase, body)

    @m.output()
    def RC_tx_close(self):
        assert self._mood
        self._RC_tx_close()

    def _RC_tx_close(self):
        self._RC.tx_close(self._mailbox, self._mood)

    @m.output()
    def dequeue(self, phase, body):
        self._pending_outbound.pop(phase, None)

    @m.output()
    def record_mood(self, mood):
        self._mood = mood

    @m.output()
    def record_mood_and_RC_tx_close(self, mood):
        self._mood = mood
        self._RC_tx_close()

    @m.output()
    def ignore_mood_and_T_mailbox_done(self, mood):
        self._T.mailbox_done()

    @m.output()
    def T_mailbox_done(self):
        self._T.mailbox_done()

    S0A.upon(connected, enter=S0B, outputs=[])
    S0A.upon(got_mailbox, enter=S1A, outputs=[record_mailbox])
    S0A.upon(add_message, enter=S0A, outputs=[queue])
    S0A.upon(close, enter=S4A, outputs=[ignore_mood_and_T_mailbox_done])
    S0B.upon(lost, enter=S0A, outputs=[])
    S0B.upon(add_message, enter=S0B, outputs=[queue])
    S0B.upon(close, enter=S4B, outputs=[ignore_mood_and_T_mailbox_done])
    S0B.upon(
        got_mailbox,
        enter=S2B,
        outputs=[record_mailbox_and_RC_tx_open_and_drain])

    S1A.upon(connected, enter=S2B, outputs=[RC_tx_open, drain])
    S1A.upon(add_message, enter=S1A, outputs=[queue])
    S1A.upon(close, enter=S4A, outputs=[ignore_mood_and_T_mailbox_done])

    S2A.upon(connected, enter=S2B, outputs=[RC_tx_open, drain])
    S2A.upon(add_message, enter=S2A, outputs=[queue])
    S2A.upon(close, enter=S3A, outputs=[record_mood])
    S2B.upon(lost, enter=S2A, outputs=[])
    S2B.upon(add_message, enter=S2B, outputs=[queue, RC_tx_add])
    S2B.upon(rx_message_theirs, enter=S2B, outputs=[N_release_and_accept])
    S2B.upon(rx_message_ours, enter=S2B, outputs=[dequeue])
    S2B.upon(close, enter=S3B, outputs=[record_mood_and_RC_tx_close])

    S3A.upon(connected, enter=S3B, outputs=[RC_tx_close])
    S3B.upon(lost, enter=S3A, outputs=[])
    S3B.upon(rx_closed, enter=S4B, outputs=[T_mailbox_done])
    S3B.upon(add_message, enter=S3B, outputs=[])
    S3B.upon(rx_message_theirs, enter=S3B, outputs=[])
    S3B.upon(rx_message_ours, enter=S3B, outputs=[])
    S3B.upon(close, enter=S3B, outputs=[])

    S4A.upon(connected, enter=S4B, outputs=[])
    S4B.upon(lost, enter=S4A, outputs=[])
    S4.upon(add_message, enter=S4, outputs=[])
    S4.upon(rx_message_theirs, enter=S4, outputs=[])
    S4.upon(rx_message_ours, enter=S4, outputs=[])
    S4.upon(close, enter=S4, outputs=[])
