from attr import attrs, attrib
from automat import MethodicalMachine

@attrs
class _Mailbox_Machine(object):
    _connection_machine = attrib()
    _m = attrib()
    m = MethodicalMachine()

    @m.state(initial=True)
    def initial(self): pass

    # all -A states: not connected
    # all -B states: yes connected
    # B states serialize as A, so they deserialize as unconnected

    # S0: know nothing
    @m.state()
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
    @m.state()
    def Ss(self): pass # terminal


    def connected(self, ws):
        self._ws = ws
        self.M_connected()

    @m.input()
    def M_start_unconnected(self): pass
    @m.input()
    def M_start_connected(self): pass
    @m.input()
    def M_set_nameplate(self): pass
    @m.input()
    def M_connected(self): pass
    @m.input()
    def M_lost(self): pass
    @m.input()
    def M_send(self, msg): pass
    @m.input()
    def M_rx_claimed(self): pass
    @m.input()
    def M_rx_msg_from_me(self, msg): pass
    @m.input()
    def M_rx_msg_from_them(self, msg): pass
    @m.input()
    def M_rx_released(self): pass
    @m.input()
    def M_rx_closed(self): pass
    @m.input()
    def M_stop(self): pass
    @m.input()
    def M_stopped(self): pass

    @m.output()
    def tx_claim(self):
        self._c.send_command("claim", nameplate=self._nameplate)
    @m.output()
    def tx_open(self): pass
    @m.output()
    def queue(self, msg): pass
    @m.output()
    def store_mailbox(self): pass # trouble(mb)
    @m.output()
    def tx_add(self, msg): pass
    @m.output()
    def tx_add_queued(self): pass
    @m.output()
    def tx_release(self): pass
    @m.output()
    def tx_close(self): pass
    @m.output()
    def process_first_msg_from_them(self, msg):
        self.tx_release()
        self.process_msg_from_them(msg)
    @m.output()
    def process_msg_from_them(self, msg): pass
    @m.output()
    def dequeue(self, msg): pass
    @m.output()
    def C_stop(self): pass
    @m.output()
    def WM_stopped(self): pass

    initial.upon(M_start_unconnected, enter=S0A, outputs=[])
    initial.upon(M_start_connected, enter=S0B, outputs=[])
    S0A.upon(M_connected, enter=S0B, outputs=[])
    S0A.upon(M_set_nameplate, enter=S1A, outputs=[])
    S0A.upon(M_stop, enter=SsB, outputs=[C_stop])
    S0B.upon(M_lost, enter=S0A, outputs=[])
    S0B.upon(M_set_nameplate, enter=S2B, outputs=[tx_claim])
    S0B.upon(M_stop, enter=SsB, outputs=[C_stop])

    S1A.upon(M_connected, enter=S2B, outputs=[tx_claim])
    S1A.upon(M_send, enter=S1A, outputs=[queue])
    S1A.upon(M_stop, enter=SsB, outputs=[C_stop])

    S2A.upon(M_connected, enter=S2B, outputs=[tx_claim])
    S2A.upon(M_stop, enter=SrA, outputs=[])
    S2A.upon(M_send, enter=S2A, outputs=[queue])
    S2B.upon(M_lost, enter=S2A, outputs=[])
    S2B.upon(M_send, enter=S2B, outputs=[queue])
    S2B.upon(M_stop, enter=SrB, outputs=[tx_release])
    S2B.upon(M_rx_claimed, enter=S3B, outputs=[store_mailbox, tx_open,
                                               tx_add_queued])

    S3A.upon(M_connected, enter=S3B, outputs=[tx_open, tx_add_queued])
    S3A.upon(M_send, enter=S3A, outputs=[queue])
    S3A.upon(M_stop, enter=SrcA, outputs=[])
    S3B.upon(M_lost, enter=S3A, outputs=[])
    S3B.upon(M_rx_msg_from_them, enter=S4B,
             outputs=[process_first_msg_from_them])
    S3B.upon(M_rx_msg_from_me, enter=S3B, outputs=[dequeue])
    S3B.upon(M_rx_claimed, enter=S3B, outputs=[])
    S3B.upon(M_send, enter=S3B, outputs=[queue, tx_add])
    S3B.upon(M_stop, enter=SrcB, outputs=[tx_release, tx_close])

    S4A.upon(M_connected, enter=S4B,
             outputs=[tx_open, tx_add_queued, tx_release])
    S4A.upon(M_send, enter=S4A, outputs=[queue])
    S4A.upon(M_stop, enter=SrcA, outputs=[])
    S4B.upon(M_lost, enter=S4A, outputs=[])
    S4B.upon(M_send, enter=S4B, outputs=[queue, tx_add])
    S4B.upon(M_rx_msg_from_them, enter=S4B, outputs=[process_msg_from_them])
    S4B.upon(M_rx_msg_from_me, enter=S4B, outputs=[dequeue])
    S4B.upon(M_rx_released, enter=S5B, outputs=[])
    S4B.upon(M_stop, enter=SrcB, outputs=[tx_release, tx_close])

    S5A.upon(M_connected, enter=S5B, outputs=[tx_open, tx_add_queued])
    S5A.upon(M_send, enter=S5A, outputs=[queue])
    S5A.upon(M_stop, enter=ScA, outputs=[])
    S5B.upon(M_lost, enter=S5A, outputs=[])
    S5B.upon(M_send, enter=S5B, outputs=[queue, tx_add])
    S5B.upon(M_rx_msg_from_them, enter=S5B, outputs=[process_msg_from_them])
    S5B.upon(M_rx_msg_from_me, enter=S5B, outputs=[dequeue])
    S5B.upon(M_stop, enter=ScB, outputs=[tx_close])

    SrcA.upon(M_connected, enter=SrcB, outputs=[tx_release, tx_close])
    SrcB.upon(M_lost, enter=SrcA, outputs=[])
    SrcB.upon(M_rx_closed, enter=SrB, outputs=[])
    SrcB.upon(M_rx_released, enter=ScB, outputs=[])

    SrB.upon(M_lost, enter=SrA, outputs=[])
    SrA.upon(M_connected, enter=SrB, outputs=[tx_release])
    SrB.upon(M_rx_released, enter=SsB, outputs=[C_stop])

    ScB.upon(M_lost, enter=ScA, outputs=[])
    ScB.upon(M_rx_closed, enter=SsB, outputs=[C_stop])
    ScA.upon(M_connected, enter=ScB, outputs=[tx_close])

    SsB.upon(M_stopped, enter=Ss, outputs=[WM_stopped])

