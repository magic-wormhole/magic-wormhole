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
    @m.setTrace()
    def set_trace(): pass # pragma: no cover

    def wire(self, boss, rendezvous_connector, lister):
        self._B = _interfaces.IBoss(boss)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._L = _interfaces.ILister(lister)

    @m.state(initial=True)
    def S0A_unknown(self): pass # pragma: no cover
    @m.state()
    def S0B_unknown_connected(self): pass # pragma: no cover
    @m.state()
    def S1A_connecting(self): pass # pragma: no cover
    @m.state()
    def S1B_allocating(self): pass # pragma: no cover
    @m.state()
    def S2_typing_nameplate(self): pass # pragma: no cover
    @m.state()
    def S3_typing_code_no_wordlist(self): pass # pragma: no cover
    @m.state()
    def S4_typing_code_wordlist(self): pass # pragma: no cover
    @m.state()
    def S5_known(self): pass # pragma: no cover

    # from App
    @m.input()
    def allocate_code(self, code_length): pass
    @m.input()
    def input_code(self, input_helper): pass
    @m.input()
    def set_code(self, code): pass

    # from RendezvousConnector
    @m.input()
    def connected(self): pass
    @m.input()
    def lost(self): pass
    @m.input()
    def rx_allocated(self, nameplate): pass

    # from Lister
    @m.input()
    def got_nameplates(self, nameplates): pass

    # from Nameplate
    @m.input()
    def got_wordlist(self, wordlist): pass

    # from CodeInputHelper
    @m.input()
    def update_nameplates(self): pass
    @m.input()
    def claim_nameplate(self, nameplate): pass
    @m.input()
    def submit_words(self, words): pass

    @m.output()
    def L_refresh_nameplates(self):
        self._L.refresh_nameplates()
    @m.output()
    def start_input_and_L_refresh_nameplates(self, input_helper):
        self._input_helper = input_helper
        self._L.refresh_nameplates()
    @m.output()
    def stash_code_length_and_RC_tx_allocate(self, code_length):
        self._code_length = code_length
        self._RC.tx_allocate()
    @m.output()
    def stash_code_length(self, code_length):
        self._code_length = code_length
    @m.output()
    def RC_tx_allocate(self):
        self._RC.tx_allocate()
    @m.output()
    def stash_wordlist(self, wordlist):
        # TODO
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
    def record_nameplate(self, nameplate):
        self._nameplate = nameplate
    @m.output()
    def N_set_nameplate(self, nameplate):
        self._N.set_nameplate(nameplate)

    @m.output()
    def generate_and_B_got_code(self, nameplate):
        self._code = make_code(nameplate, self._code_length)
        self._B_got_code()

    @m.output()
    def submit_words_and_B_got_code(self, words):
        assert self._nameplate
        self._code = self._nameplate + "-" + words
        self._B_got_code()

    @m.output()
    def B_got_code(self, code):
        self._code = code
        self._B_got_code()

    def _B_got_code(self):
        self._B.got_code(self._code)

    S0A_unknown.upon(connected, enter=S0B_unknown_connected, outputs=[])
    S0B_unknown_connected.upon(lost, enter=S0A_unknown, outputs=[])

    S0A_unknown.upon(set_code, enter=S5_known, outputs=[B_got_code])
    S0B_unknown_connected.upon(set_code, enter=S5_known, outputs=[B_got_code])

    S0A_unknown.upon(allocate_code, enter=S1A_connecting,
                     outputs=[stash_code_length])
    S0B_unknown_connected.upon(allocate_code, enter=S1B_allocating,
                               outputs=[stash_code_length_and_RC_tx_allocate])
    S1A_connecting.upon(connected, enter=S1B_allocating,
                        outputs=[RC_tx_allocate])
    S1B_allocating.upon(lost, enter=S1A_connecting, outputs=[])
    S1B_allocating.upon(rx_allocated, enter=S5_known,
                        outputs=[generate_and_B_got_code])

    S0A_unknown.upon(input_code, enter=S2_typing_nameplate,
                     outputs=[start_input_and_L_refresh_nameplates])
    S0B_unknown_connected.upon(input_code, enter=S2_typing_nameplate,
                               outputs=[start_input_and_L_refresh_nameplates])
    S2_typing_nameplate.upon(update_nameplates, enter=S2_typing_nameplate,
                             outputs=[L_refresh_nameplates])
    S2_typing_nameplate.upon(got_nameplates,
                             enter=S2_typing_nameplate,
                             outputs=[stash_nameplates])
    S2_typing_nameplate.upon(claim_nameplate, enter=S3_typing_code_no_wordlist,
                             outputs=[record_nameplate, N_set_nameplate])
    S2_typing_nameplate.upon(connected, enter=S2_typing_nameplate, outputs=[])
    S2_typing_nameplate.upon(lost, enter=S2_typing_nameplate, outputs=[])

    S3_typing_code_no_wordlist.upon(got_wordlist,
                                    enter=S4_typing_code_wordlist,
                                    outputs=[stash_wordlist])
    S3_typing_code_no_wordlist.upon(submit_words, enter=S5_known,
                                    outputs=[submit_words_and_B_got_code])
    S3_typing_code_no_wordlist.upon(connected, enter=S3_typing_code_no_wordlist,
                                    outputs=[])
    S3_typing_code_no_wordlist.upon(lost, enter=S3_typing_code_no_wordlist,
                                    outputs=[])

    S4_typing_code_wordlist.upon(submit_words, enter=S5_known,
                                 outputs=[submit_words_and_B_got_code])
    S4_typing_code_wordlist.upon(connected, enter=S4_typing_code_wordlist,
                                 outputs=[])
    S4_typing_code_wordlist.upon(lost, enter=S4_typing_code_wordlist,
                                 outputs=[])

    S5_known.upon(connected, enter=S5_known, outputs=[])
    S5_known.upon(lost, enter=S5_known, outputs=[])
