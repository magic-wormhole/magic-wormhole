from __future__ import print_function, absolute_import, unicode_literals
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides
from automat import MethodicalMachine
from . import _interfaces

@attrs
@implementer(_interfaces.IInput)
class Input(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    @m.setTrace()
    def set_trace(): pass # pragma: no cover

    def __attrs_post_init__(self):
        self._nameplate = None
        self._wordlist = None
        self._claimed_waiter = None

    def wire(self, code, lister):
        self._C = _interfaces.ICode(code)
        self._L = _interfaces.ILister(lister)

    @m.state(initial=True)
    def S0_idle(self): pass # pragma: no cover
    @m.state()
    def S1_nameplate(self): pass # pragma: no cover
    @m.state()
    def S2_code_no_wordlist(self): pass # pragma: no cover
    @m.state()
    def S3_code_yes_wordlist(self): pass # pragma: no cover
    @m.state(terminal=True)
    def S4_done(self): pass # pragma: no cover

    # from Code
    @m.input()
    def start(self): pass

    # from Lister
    @m.input()
    def got_nameplates(self, nameplates): pass

    # from Nameplate??
    @m.input()
    def got_wordlist(self, wordlist): pass

    # API provided to app as ICodeInputHelper
    @m.input()
    def refresh_nameplates(self): pass
    @m.input()
    def _choose_nameplate(self, nameplate): pass
    @m.input()
    def choose_words(self, words): pass

    @m.output()
    def L_refresh_nameplates(self):
        self._L.refresh_nameplates()
    @m.output()
    def start_and_L_refresh_nameplates(self, input_helper):
        self._input_helper = input_helper
        self._L.refresh_nameplates()
    @m.output()
    def stash_wordlist_and_notify(self, wordlist):
        self._wordlist = wordlist
        if self._claimed_waiter:
            self._claimed_waiter.callback(None)
            del self._claimed_waiter
    @m.output()
    def stash_nameplate(self, nameplate):
        self._nameplate = nameplate
    @m.output()
    def C_got_nameplate(self, nameplate):
        self._C.got_nameplate(nameplate)

    @m.output()
    def finished(self, words):
        code = self._nameplate + "-" + words
        self._C.finished_input(code)

    S0_idle.upon(start, enter=S1_nameplate, outputs=[L_refresh_nameplates])
    S1_nameplate.upon(refresh_nameplates, enter=S1_nameplate,
                      outputs=[L_refresh_nameplates])
    S1_nameplate.upon(_choose_nameplate, enter=S2_code_no_wordlist,
                      outputs=[stash_nameplate, C_got_nameplate])
    S2_code_no_wordlist.upon(got_wordlist, enter=S3_code_yes_wordlist,
                             outputs=[stash_wordlist_and_notify])
    S2_code_no_wordlist.upon(choose_words, enter=S4_done, outputs=[finished])
    S3_code_yes_wordlist.upon(choose_words, enter=S4_done, outputs=[finished])

    # methods for the CodeInputHelper to use
    #refresh_nameplates/_choose_nameplate/choose_words: @m.input methods

    def get_nameplate_completions(self, prefix):
        completions = []
        for nameplate in self._nameplates
        pass
    def choose_nameplate(self, nameplate):
        if self._claimed_waiter is not None:
            raise X
        d = self._claimed_waiter = defer.Deferred()
        self._choose_nameplate(nameplate)

    def get_word_completions(self, prefix):
        pass
    
