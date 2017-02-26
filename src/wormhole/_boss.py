from __future__ import print_function, absolute_import, unicode_literals
import re
import six
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides, instance_of
from twisted.python import log
from automat import MethodicalMachine
from . import _interfaces
from ._nameplate import Nameplate
from ._mailbox import Mailbox
from ._send import Send
from ._order import Order
from ._key import Key
from ._receive import Receive
from ._rendezvous import RendezvousConnector
from ._nameplate_lister import NameplateListing
from ._code import Code
from ._terminator import Terminator
from .errors import WrongPasswordError
from .util import bytes_to_dict

class WormholeError(Exception):
    pass

@attrs
@implementer(_interfaces.IBoss)
class Boss(object):
    _W = attrib()
    _side = attrib(validator=instance_of(type(u"")))
    _url = attrib(validator=instance_of(type(u"")))
    _appid = attrib(validator=instance_of(type(u"")))
    _reactor = attrib()
    _journal = attrib(validator=provides(_interfaces.IJournal))
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()

    def __attrs_post_init__(self):
        self._N = Nameplate()
        self._M = Mailbox(self._side)
        self._S = Send(self._side, self._timing)
        self._O = Order(self._side, self._timing)
        self._K = Key(self._appid, self._side, self._timing)
        self._R = Receive(self._side, self._timing)
        self._RC = RendezvousConnector(self._url, self._appid, self._side,
                                       self._reactor, self._journal,
                                       self._timing)
        self._NL = NameplateListing()
        self._C = Code(self._timing)
        self._T = Terminator()

        self._N.wire(self._M, self._RC, self._T)
        self._M.wire(self._N, self._RC, self._O, self._T)
        self._S.wire(self._M)
        self._O.wire(self._K, self._R)
        self._K.wire(self, self._M, self._R)
        self._R.wire(self, self._K, self._S)
        self._RC.wire(self, self._M, self._C, self._NL)
        self._NL.wire(self._RC, self._C)
        self._C.wire(self, self._RC, self._NL)
        self._T.wire(self, self._RC, self._N, self._M)

        self._next_tx_phase = 0
        self._next_rx_phase = 0
        self._rx_phases = {} # phase -> plaintext

        self._result = "empty"

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

    # from the Wormhole

    # input/allocate/set_code are regular methods, not state-transition
    # inputs. We expect them to be called just after initialization, while
    # we're in the S0_empty state. You must call exactly one of them, and the
    # call must happen while we're in S0_empty, which makes them good
    # candiates for being a proper @m.input, but set_code() will immediately
    # (reentrantly) cause self.got_code() to be fired, which is messy. These
    # are all passthroughs to the Code machine, so one alternative would be
    # to have Wormhole call Code.{input,allocate,set_code} instead, but that
    # would require the Wormhole to be aware of Code (whereas right now
    # Wormhole only knows about this Boss instance, and everything else is
    # hidden away).
    def input_code(self, stdio):
        self._C.input_code(stdio)
    def allocate_code(self, code_length):
        self._C.allocate_code(code_length)
    def set_code(self, code):
        self._C.set_code(code)

    @m.input()
    def send(self, plaintext): pass
    @m.input()
    def close(self): pass

    # from RendezvousConnector
    @m.input()
    def rx_welcome(self, welcome): pass

    # from Code (provoked by input/allocate/set_code)
    @m.input()
    def got_code(self, code): pass

    # Key sends (got_verifier, scared)
    # Receive sends (got_message, happy, scared)
    @m.input()
    def happy(self): pass
    @m.input()
    def scared(self): pass
    @m.input()
    def rx_error(self, err, orig): pass

    def got_message(self, phase, plaintext):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(plaintext, type(b"")), type(plaintext)
        if phase == "version":
            self.got_version(plaintext)
        elif re.search(r'^\d+$', phase):
            self.got_phase(int(phase), plaintext)
        else:
            # Ignore unrecognized phases, for forwards-compatibility. Use
            # log.err so tests will catch surprises.
            log.err("received unknown phase '%s'" % phase)
    @m.input()
    def got_version(self, plaintext): pass
    @m.input()
    def got_phase(self, phase, plaintext): pass
    @m.input()
    def got_verifier(self, verifier): pass

    # Terminator sends closed
    @m.input()
    def closed(self): pass


    @m.output()
    def process_welcome(self, welcome):
        pass # TODO: ignored for now

    @m.output()
    def do_got_code(self, code):
        nameplate = code.split("-")[0]
        self._N.set_nameplate(nameplate)
        self._K.got_code(code)
        self._W.got_code(code)
    @m.output()
    def process_version(self, plaintext):
        self._their_versions = bytes_to_dict(plaintext)
        # ignored for now

    @m.output()
    def S_send(self, plaintext):
        assert isinstance(plaintext, type(b"")), type(plaintext)
        phase = self._next_tx_phase
        self._next_tx_phase += 1
        self._S.send("%d" % phase, plaintext)

    @m.output()
    def close_error(self, err, orig):
        self._result = WormholeError(err)
        self._T.close("errory")
    @m.output()
    def close_scared(self):
        self._result = WrongPasswordError()
        self._T.close("scary")
    @m.output()
    def close_lonely(self):
        self._result = WormholeError("lonely")
        self._T.close("lonely")
    @m.output()
    def close_happy(self):
        self._result = "happy"
        self._T.close("happy")

    @m.output()
    def W_got_verifier(self, verifier):
        self._W.got_verifier(verifier)
    @m.output()
    def W_received(self, phase, plaintext):
        assert isinstance(phase, six.integer_types), type(phase)
        # we call Wormhole.received() in strict phase order, with no gaps
        self._rx_phases[phase] = plaintext
        while self._next_rx_phase in self._rx_phases:
            self._W.received(self._rx_phases.pop(self._next_rx_phase))
            self._next_rx_phase += 1

    @m.output()
    def W_closed(self):
        self._W.closed(self._result)

    S0_empty.upon(close, enter=S3_closing, outputs=[close_lonely])
    S0_empty.upon(send, enter=S0_empty, outputs=[S_send])
    S0_empty.upon(rx_welcome, enter=S0_empty, outputs=[process_welcome])
    S0_empty.upon(got_code, enter=S1_lonely, outputs=[do_got_code])
    S0_empty.upon(rx_error, enter=S3_closing, outputs=[close_error])
    S1_lonely.upon(rx_welcome, enter=S1_lonely, outputs=[process_welcome])
    S1_lonely.upon(happy, enter=S2_happy, outputs=[])
    S1_lonely.upon(scared, enter=S3_closing, outputs=[close_scared])
    S1_lonely.upon(close, enter=S3_closing, outputs=[close_lonely])
    S1_lonely.upon(send, enter=S1_lonely, outputs=[S_send])
    S1_lonely.upon(got_verifier, enter=S1_lonely, outputs=[W_got_verifier])
    S1_lonely.upon(rx_error, enter=S3_closing, outputs=[close_error])
    S2_happy.upon(rx_welcome, enter=S2_happy, outputs=[process_welcome])
    S2_happy.upon(got_phase, enter=S2_happy, outputs=[W_received])
    S2_happy.upon(got_version, enter=S2_happy, outputs=[process_version])
    S2_happy.upon(scared, enter=S3_closing, outputs=[close_scared])
    S2_happy.upon(close, enter=S3_closing, outputs=[close_happy])
    S2_happy.upon(send, enter=S2_happy, outputs=[S_send])
    S2_happy.upon(rx_error, enter=S3_closing, outputs=[close_error])

    S3_closing.upon(rx_welcome, enter=S3_closing, outputs=[])
    S3_closing.upon(rx_error, enter=S3_closing, outputs=[])
    S3_closing.upon(got_phase, enter=S3_closing, outputs=[])
    S3_closing.upon(got_version, enter=S3_closing, outputs=[])
    S3_closing.upon(happy, enter=S3_closing, outputs=[])
    S3_closing.upon(scared, enter=S3_closing, outputs=[])
    S3_closing.upon(close, enter=S3_closing, outputs=[])
    S3_closing.upon(send, enter=S3_closing, outputs=[])
    S3_closing.upon(closed, enter=S4_closed, outputs=[W_closed])

    S4_closed.upon(rx_welcome, enter=S4_closed, outputs=[])
    S4_closed.upon(got_phase, enter=S4_closed, outputs=[])
    S4_closed.upon(got_version, enter=S4_closed, outputs=[])
    S4_closed.upon(happy, enter=S4_closed, outputs=[])
    S4_closed.upon(scared, enter=S4_closed, outputs=[])
    S4_closed.upon(close, enter=S4_closed, outputs=[])
    S4_closed.upon(send, enter=S4_closed, outputs=[])

