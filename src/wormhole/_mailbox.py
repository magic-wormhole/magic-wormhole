from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import instance_of
from automat import MethodicalMachine
from . import _interfaces

@attrs
@implementer(_interfaces.IMailbox)
class Mailbox(object):
    _side = attrib(validator=instance_of(type(u"")))
    m = MethodicalMachine()

    def __init__(self):
        self._mood = None
        self._nameplate = None
        self._mailbox = None
        self._pending_outbound = {}
        self._processed = set()

    def wire(self, boss, rendezvous_connector, ordering):
        self._B = _interfaces.IBoss(boss)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._O = _interfaces.IOrder(ordering)

    # all -A states: not connected
    # all -B states: yes connected
    # B states serialize as A, so they deserialize as unconnected

    # S0: know nothing
    @m.state(initial=True)
    def S0A(self): pass
    @m.state()
    def S0B(self): pass

    # S1: nameplate known, not claimed
    @m.state()
    def S1A(self): pass

    # S2: nameplate known, maybe claimed
    @m.state()
    def S2A(self): pass
    @m.state()
    def S2B(self): pass

    # S3: nameplate claimed, mailbox known, maybe open
    @m.state()
    def S3A(self): pass
    @m.state()
    def S3B(self): pass

    # S4: mailbox maybe open, nameplate maybe released
    # We've definitely opened the mailbox at least once, but it must be
    # re-opened with each connection, because open() is also subscribe()
    @m.state()
    def S4A(self): pass
    @m.state()
    def S4B(self): pass

    # S5: mailbox maybe open, nameplate released
    @m.state()
    def S5A(self): pass
    @m.state()
    def S5B(self): pass

    # Src: waiting for release+close
    @m.state()
    def SrcA(self): pass
    @m.state()
    def SrcB(self): pass
    # Sr: closed (or never opened), waiting for release
    @m.state()
    def SrA(self): pass
    @m.state()
    def SrB(self): pass
    # Sc: released (or never claimed), waiting for close
    @m.state()
    def ScA(self): pass
    @m.state()
    def ScB(self): pass
    # Ss: closed and released, waiting for stop
    @m.state()
    def SsB(self): pass
    @m.state(terminal=True)
    def Ss(self): pass


    # from Boss
    @m.input()
    def set_nameplate(self, nameplate): pass
    @m.input()
    def close(self, mood): pass

    # from RendezvousConnector
    @m.input()
    def connected(self): pass
    @m.input()
    def lost(self): pass

    @m.input()
    def rx_claimed(self, mailbox): pass

    def rx_message(self, side, phase, body):
        assert isinstance(side, type("")), type(side)
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        if side == self._side:
            self.rx_message_ours(phase, body)
        else:
            self.rx_message_theirs(phase, body)
    @m.input()
    def rx_message_ours(self, phase, body): pass
    @m.input()
    def rx_message_theirs(self, phase, body): pass
    @m.input()
    def rx_released(self): pass
    @m.input()
    def rx_closed(self): pass
    @m.input()
    def stopped(self): pass

    # from Send or Key
    @m.input()
    def add_message(self, phase, body): pass


    @m.output()
    def record_nameplate(self, nameplate):
        self._nameplate = nameplate
    @m.output()
    def record_nameplate_and_RC_tx_claim(self, nameplate):
        self._nameplate = nameplate
        self._RX.tx_claim(self._nameplate)
    @m.output()
    def RC_tx_claim(self):
        # when invoked via M.connected(), we must use the stored nameplate
        self._RC.tx_claim(self._nameplate)
    @m.output()
    def RC_tx_open(self):
        assert self._mailbox
        self._RC.tx_open(self._mailbox)
    @m.output()
    def queue(self, phase, body):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        self._pending_outbound[phase] = body
    @m.output()
    def store_mailbox_and_RC_tx_open_and_drain(self, mailbox):
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
    def RC_tx_release(self):
        self._RC.tx_release()
    @m.output()
    def RC_tx_release_and_accept(self, phase, body):
        self._RC.tx_release()
        self._accept(phase, body)
    @m.output()
    def record_mood_and_RC_tx_release(self, mood):
        self._mood = mood
        self._RC.tx_release()
    @m.output()
    def record_mood_and_RC_tx_release_and_RC_tx_close(self, mood):
        self._mood = mood
        self._RC.tx_release()
        self._RC.tx_close(self._mood)
    @m.output()
    def RC_tx_close(self):
        assert self._mood
        self._RC.tx_close(self._mood)
    @m.output()
    def record_mood_and_RC_tx_close(self, mood):
        self._mood = mood
        self._RC.tx_close(self._mood)
    @m.output()
    def accept(self, phase, body):
        self._accept(phase, body)
    def _accept(self, phase, body):
        if phase not in self._processed:
            self._O.got_message(phase, body)
            self._processed.add(phase)
    @m.output()
    def dequeue(self, phase, body):
        self._pending_outbound.pop(phase)
    @m.output()
    def record_mood(self, mood):
        self._mood = mood
    @m.output()
    def record_mood_and_RC_stop(self, mood):
        self._mood = mood
        self._RC_stop()
    @m.output()
    def RC_stop(self):
        self._RC_stop()
    def _RC_stop(self):
        self._RC.stop()
    @m.output()
    def W_closed(self):
        self._B.closed()

    S0A.upon(connected, enter=S0B, outputs=[])
    S0A.upon(set_nameplate, enter=S1A, outputs=[record_nameplate])
    S0A.upon(add_message, enter=S0A, outputs=[queue])
    S0B.upon(lost, enter=S0A, outputs=[])
    S0B.upon(set_nameplate, enter=S2B, outputs=[record_nameplate_and_RC_tx_claim])
    S0B.upon(add_message, enter=S0B, outputs=[queue])

    S1A.upon(connected, enter=S2B, outputs=[RC_tx_claim])
    S1A.upon(add_message, enter=S1A, outputs=[queue])

    S2A.upon(connected, enter=S2B, outputs=[RC_tx_claim])
    S2A.upon(add_message, enter=S2A, outputs=[queue])
    S2B.upon(lost, enter=S2A, outputs=[])
    S2B.upon(add_message, enter=S2B, outputs=[queue])
    S2B.upon(rx_claimed, enter=S3B,
             outputs=[store_mailbox_and_RC_tx_open_and_drain])

    S3A.upon(connected, enter=S3B, outputs=[RC_tx_open, drain])
    S3A.upon(add_message, enter=S3A, outputs=[queue])
    S3B.upon(lost, enter=S3A, outputs=[])
    S3B.upon(rx_message_theirs, enter=S4B, outputs=[RC_tx_release_and_accept])
    S3B.upon(rx_message_ours, enter=S3B, outputs=[dequeue])
    S3B.upon(rx_claimed, enter=S3B, outputs=[])
    S3B.upon(add_message, enter=S3B, outputs=[queue, RC_tx_add])

    S4A.upon(connected, enter=S4B,
             outputs=[RC_tx_open, drain, RC_tx_release])
    S4A.upon(add_message, enter=S4A, outputs=[queue])
    S4B.upon(lost, enter=S4A, outputs=[])
    S4B.upon(add_message, enter=S4B, outputs=[queue, RC_tx_add])
    S4B.upon(rx_message_theirs, enter=S4B, outputs=[accept])
    S4B.upon(rx_message_ours, enter=S4B, outputs=[dequeue])
    S4B.upon(rx_released, enter=S5B, outputs=[])

    S5A.upon(connected, enter=S5B, outputs=[RC_tx_open, drain])
    S5A.upon(add_message, enter=S5A, outputs=[queue])
    S5B.upon(lost, enter=S5A, outputs=[])
    S5B.upon(add_message, enter=S5B, outputs=[queue, RC_tx_add])
    S5B.upon(rx_message_theirs, enter=S5B, outputs=[accept])
    S5B.upon(rx_message_ours, enter=S5B, outputs=[dequeue])

    if True:
        S0A.upon(close, enter=SsB, outputs=[record_mood_and_RC_stop])
        S0B.upon(close, enter=SsB, outputs=[record_mood_and_RC_stop])
        S1A.upon(close, enter=SsB, outputs=[record_mood_and_RC_stop])
        S2A.upon(close, enter=SrA, outputs=[record_mood])
        S2B.upon(close, enter=SrB, outputs=[record_mood_and_RC_tx_release])
        S3A.upon(close, enter=SrcA, outputs=[record_mood])
        S3B.upon(close, enter=SrcB,
                 outputs=[record_mood_and_RC_tx_release_and_RC_tx_close])
        S4A.upon(close, enter=SrcA, outputs=[record_mood])
        S4B.upon(close, enter=SrcB,
                 outputs=[record_mood_and_RC_tx_release_and_RC_tx_close])
        S5A.upon(close, enter=ScA, outputs=[record_mood])
        S5B.upon(close, enter=ScB, outputs=[record_mood_and_RC_tx_close])

        SrcA.upon(connected, enter=SrcB, outputs=[RC_tx_release, RC_tx_close])
        SrcB.upon(lost, enter=SrcA, outputs=[])
        SrcB.upon(rx_closed, enter=SrB, outputs=[])
        SrcB.upon(rx_released, enter=ScB, outputs=[])

        SrB.upon(lost, enter=SrA, outputs=[])
        SrA.upon(connected, enter=SrB, outputs=[RC_tx_release])
        SrB.upon(rx_released, enter=SsB, outputs=[RC_stop])

        ScB.upon(lost, enter=ScA, outputs=[])
        ScB.upon(rx_closed, enter=SsB, outputs=[RC_stop])
        ScA.upon(connected, enter=ScB, outputs=[RC_tx_close])

        SsB.upon(stopped, enter=Ss, outputs=[W_closed])

