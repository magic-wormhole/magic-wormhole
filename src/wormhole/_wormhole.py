from zope.interface import implementer
from automat import MethodicalMachine
from . import _interfaces
from ._mailbox import Mailbox
from ._send import Send
from ._order import Order
from ._key import Key
from ._receive import Receive
from ._rendezvous import RendezvousConnector
from ._nameplate import NameplateListing
from ._code import Code
from .util import bytes_to_dict

@implementer(_interfaces.IWormhole)
class Wormhole:
    m = MethodicalMachine()

    def __init__(self, side, reactor, timing):
        self._reactor = reactor

        self._M = Mailbox(side)
        self._S = Send(side, timing)
        self._O = Order(side, timing)
        self._K = Key(timing)
        self._R = Receive(side, timing)
        self._RC = RendezvousConnector(side, timing, reactor)
        self._NL = NameplateListing()
        self._C = Code(timing)

        self._M.wire(self, self._RC, self._O)
        self._S.wire(self._M)
        self._O.wire(self._K, self._R)
        self._K.wire(self, self._M, self._R)
        self._R.wire(self, self._K, self._S)
        self._RC.wire(self, self._M, self._C, self._NL)
        self._NL.wire(self._RC, self._C)
        self._C.wire(self, self._RC, self._NL)

    # these methods are called from outside
    def start(self):
        self._RC.start()

    # and these are the state-machine transition functions, which don't take
    # args
    @m.state(initial=True)
    def S0_empty(self): pass
    @m.state()
    def S1_lonely(self): pass
    @m.state()
    def S2_happy(self): pass
    @m.state()
    def S3_closing(self): pass
    @m.state(terminal=True)
    def S4_closed(self): pass

    # from the Application, or some sort of top-level shim
    @m.input()
    def send(self, phase, plaintext): pass
    @m.input()
    def close(self): pass

    # from Code (which may be provoked by the Application)
    @m.input()
    def set_code(self, code): pass

    # Key sends (got_verifier, scared)
    # Receive sends (got_message, happy, scared)
    @m.input()
    def happy(self): pass
    @m.input()
    def scared(self): pass
    def got_message(self, phase, plaintext):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(plaintext, type(b"")), type(plaintext)
        if phase == "version":
            self.got_version(plaintext)
        else:
            self.got_phase(phase, plaintext)
    @m.input()
    def got_version(self, version): pass
    @m.input()
    def got_phase(self, phase, plaintext): pass
    @m.input()
    def got_verifier(self, verifier): pass

    # Mailbox sends closed
    @m.input()
    def closed(self): pass


    @m.output()
    def got_code(self, code):
        nameplate = code.split("-")[0]
        self._M.set_nameplate(nameplate)
        self._K.set_code(code)
    @m.output()
    def process_version(self, plaintext):
        self._their_versions = bytes_to_dict(plaintext)
        # ignored for now

    @m.output()
    def S_send(self, phase, plaintext):
        self._S.send(phase, plaintext)

    @m.output()
    def close_scared(self):
        self._M.close("scary")
    @m.output()
    def close_lonely(self):
        self._M.close("lonely")
    @m.output()
    def close_happy(self):
        self._M.close("happy")

    @m.output()
    def A_received(self, phase, plaintext):
        self._A.received(phase, plaintext)
    @m.output()
    def A_got_verifier(self, verifier):
        self._A.got_verifier(verifier)

    @m.output()
    def A_closed(self):
        result = "???"
        self._A.closed(result)

    S0_empty.upon(send, enter=S0_empty, outputs=[S_send])
    S0_empty.upon(set_code, enter=S1_lonely, outputs=[got_code])
    S1_lonely.upon(happy, enter=S2_happy, outputs=[])
    S1_lonely.upon(scared, enter=S3_closing, outputs=[close_scared])
    S1_lonely.upon(close, enter=S3_closing, outputs=[close_lonely])
    S1_lonely.upon(send, enter=S1_lonely, outputs=[S_send])
    S1_lonely.upon(got_verifier, enter=S1_lonely, outputs=[A_got_verifier])
    S2_happy.upon(got_phase, enter=S2_happy, outputs=[A_received])
    S2_happy.upon(got_version, enter=S2_happy, outputs=[process_version])
    S2_happy.upon(scared, enter=S3_closing, outputs=[close_scared])
    S2_happy.upon(close, enter=S3_closing, outputs=[close_happy])
    S2_happy.upon(send, enter=S2_happy, outputs=[S_send])

    S3_closing.upon(got_phase, enter=S3_closing, outputs=[])
    S3_closing.upon(got_version, enter=S3_closing, outputs=[])
    S3_closing.upon(happy, enter=S3_closing, outputs=[])
    S3_closing.upon(scared, enter=S3_closing, outputs=[])
    S3_closing.upon(close, enter=S3_closing, outputs=[])
    S3_closing.upon(send, enter=S3_closing, outputs=[])
    S3_closing.upon(closed, enter=S4_closed, outputs=[A_closed])

    S4_closed.upon(got_phase, enter=S4_closed, outputs=[])
    S4_closed.upon(got_version, enter=S4_closed, outputs=[])
    S4_closed.upon(happy, enter=S4_closed, outputs=[])
    S4_closed.upon(scared, enter=S4_closed, outputs=[])
    S4_closed.upon(close, enter=S4_closed, outputs=[])
    S4_closed.upon(send, enter=S4_closed, outputs=[])

