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

    def wire(self, wormhole, rendezvous_connector, nameplate_lister):
        self._W = _interfaces.IWormhole(wormhole)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._NL = _interfaces.INameplateLister(nameplate_lister)

    @m.state(initial=True)
    def S0_unknown(self): pass
    @m.state()
    def S1_allocating(self): pass
    @m.state()
    def S2_typing_nameplate(self): pass
    @m.state()
    def S3_typing_code(self): pass
    @m.state()
    def S4_known(self): pass

    # from App
    @m.input()
    def allocate(self, code_length): pass
    @m.input()
    def input(self, stdio): pass
    @m.input()
    def set(self, code): pass

    # from RendezvousConnector
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
    def RC_tx_allocate(self, code_length):
        self._code_length = code_length
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
    def generate_and_set(self, nameplate):
        self._code = make_code(nameplate, self._code_length)
        self._W_got_code()

    @m.output()
    def W_got_code(self, code):
        self._code = code
        self._W_got_code()

    def _W_got_code(self):
        self._W.got_code(self._code)

    S0_unknown.upon(allocate, enter=S1_allocating, outputs=[RC_tx_allocate])
    S1_allocating.upon(rx_allocated, enter=S4_known, outputs=[generate_and_set])

    S0_unknown.upon(set, enter=S4_known, outputs=[W_got_code])

    S0_unknown.upon(input, enter=S2_typing_nameplate,
                    outputs=[start_input_and_NL_refresh_nameplates])
    S2_typing_nameplate.upon(tab, enter=S2_typing_nameplate,
                             outputs=[do_completion_nameplates])
    S2_typing_nameplate.upon(got_nameplates, enter=S2_typing_nameplate,
                             outputs=[stash_nameplates])
    S2_typing_nameplate.upon(hyphen, enter=S3_typing_code,
                             outputs=[lookup_wordlist])
    S3_typing_code.upon(tab, enter=S3_typing_code, outputs=[do_completion_code])
    S3_typing_code.upon(RETURN, enter=S4_known, outputs=[W_got_code])
