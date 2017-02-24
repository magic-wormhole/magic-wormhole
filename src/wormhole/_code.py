from __future__ import print_function, absolute_import, unicode_literals
import os
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides
from automat import MethodicalMachine
from . import _interfaces
from .wordlist import (byte_to_even_word, byte_to_odd_word,
                       #even_words_lowercase, odd_words_lowercase,
                       )

def make_code(nameplate, code_length):
    assert isinstance(nameplate, type("")), type(nameplate)
    words = []
    for i in range(code_length):
        # we start with an "odd word"
        if i % 2 == 0:
            words.append(byte_to_odd_word[os.urandom(1)].lower())
        else:
            words.append(byte_to_even_word[os.urandom(1)].lower())
    return "%s-%s" % (nameplate, "-".join(words))

@attrs
@implementer(_interfaces.ICode)
class Code(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()

    def wire(self, boss, rendezvous_connector, nameplate_lister):
        self._B = _interfaces.IBoss(boss)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._NL = _interfaces.INameplateLister(nameplate_lister)

    @m.state(initial=True)
    def S0_unknown(self): pass
    @m.state()
    def S1A_connecting(self): pass
    @m.state()
    def S1B_allocating(self): pass
    @m.state()
    def S2_typing_nameplate(self): pass
    @m.state()
    def S3_typing_code(self): pass
    @m.state()
    def S4_known(self): pass

    # from App
    @m.input()
    def allocate_code(self, code_length): pass
    @m.input()
    def input_code(self, stdio): pass
    @m.input()
    def set_code(self, code): pass

    # from RendezvousConnector
    @m.input()
    def connected(self): pass
    @m.input()
    def lost(self): pass
    @m.input()
    def rx_allocated(self, nameplate): pass

    # from NameplateLister
    @m.input()
    def got_nameplates(self, nameplates): pass

    # from stdin/readline/???
    @m.input()
    def tab(self): pass
    @m.input()
    def hyphen(self): pass
    @m.input()
    def RETURN(self, code): pass

    @m.output()
    def NL_refresh_nameplates(self):
        self._NL.refresh_nameplates()
    @m.output()
    def start_input_and_NL_refresh_nameplates(self, stdio):
        self._stdio = stdio
        self._NL.refresh_nameplates()
    @m.output()
    def stash_code_length(self, code_length):
        self._code_length = code_length
    @m.output()
    def RC_tx_allocate(self):
        self._RC.tx_allocate()
    @m.output()
    def do_completion_nameplates(self):
        pass
    @m.output()
    def stash_nameplates(self, nameplates):
        self._known_nameplates = nameplates
        pass
    @m.output()
    def lookup_wordlist(self):
        pass
    @m.output()
    def do_completion_code(self):
        pass
    @m.output()
    def generate_and_B_got_code(self, nameplate):
        self._code = make_code(nameplate, self._code_length)
        self._B_got_code()

    @m.output()
    def B_got_code(self, code):
        self._code = code
        self._B_got_code()

    def _B_got_code(self):
        self._B.got_code(self._code)

    S0_unknown.upon(set_code, enter=S4_known, outputs=[B_got_code])

    S0_unknown.upon(allocate_code, enter=S1A_connecting,
                    outputs=[stash_code_length])
    S1A_connecting.upon(connected, enter=S1B_allocating,
                        outputs=[RC_tx_allocate])
    S1B_allocating.upon(lost, enter=S1A_connecting, outputs=[])
    S1B_allocating.upon(rx_allocated, enter=S4_known,
                        outputs=[generate_and_B_got_code])

    S0_unknown.upon(input_code, enter=S2_typing_nameplate,
                    outputs=[start_input_and_NL_refresh_nameplates])
    S2_typing_nameplate.upon(tab, enter=S2_typing_nameplate,
                             outputs=[do_completion_nameplates])
    S2_typing_nameplate.upon(got_nameplates, enter=S2_typing_nameplate,
                             outputs=[stash_nameplates])
    S2_typing_nameplate.upon(hyphen, enter=S3_typing_code,
                             outputs=[lookup_wordlist])
    # TODO: need a proper pair of connected/lost states around S2
    S2_typing_nameplate.upon(connected, enter=S2_typing_nameplate, outputs=[])
    S2_typing_nameplate.upon(lost, enter=S2_typing_nameplate, outputs=[])

    S3_typing_code.upon(tab, enter=S3_typing_code, outputs=[do_completion_code])
    S3_typing_code.upon(RETURN, enter=S4_known, outputs=[B_got_code])
    S3_typing_code.upon(connected, enter=S3_typing_code, outputs=[])
    S3_typing_code.upon(lost, enter=S3_typing_code, outputs=[])

    S4_known.upon(connected, enter=S4_known, outputs=[])
    S4_known.upon(lost, enter=S4_known, outputs=[])
