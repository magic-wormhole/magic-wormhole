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

    def wire(self, code, lister):
        self._C = _interfaces.ICode(code)
        self._L = _interfaces.ILister(lister)

    @m.state(initial=True)
    def S0_idle(self): pass # pragma: no cover
    @m.state()
    def S1_typing_nameplate(self): pass # pragma: no cover
    @m.state()
    def S2_typing_code_no_wordlist(self): pass # pragma: no cover
    @m.state()
    def S3_typing_code_yes_wordlist(self): pass # pragma: no cover
    @m.state(terminal=True)
    def S4_done(self): pass # pragma: no cover

    # from Code
    @m.input()
    def start(self, input_helper): pass

    # from Lister
    @m.input()
    def got_nameplates(self, nameplates): pass

    # from Nameplate
    @m.input()
    def got_wordlist(self, wordlist): pass

    # API provided to app as ICodeInputHelper
    @m.input()
    def refresh_nameplates(self): pass
    @m.input()
    def choose_nameplate(self, nameplate): pass
    @m.input()
    def choose_words(self, words): pass

    @m.output()
    def do_start(self, input_helper):
        self._input_helper = input_helper
        self._L.refresh_nameplates()
    @m.output()
    def do_refresh(self):
        self._L.refresh_nameplates()
    @m.output()
    def do_nameplate(self, nameplate):
        self._nameplate = nameplate
        self._C.got_nameplate(nameplate)
    @m.output()
    def do_wordlist(self, wordlist):
        self._wordlist = wordlist

    @m.output()
    def do_words(self, words):
        code = self._nameplate + "-" + words
        self._C.finished_input(code)

    S0_idle.upon(start, enter=S1_typing_nameplate, outputs=[do_start])
    S1_typing_nameplate.upon(refresh_nameplates, enter=S1_typing_nameplate,
                             outputs=[do_refresh])
    S1_typing_nameplate.upon(choose_nameplate, enter=S2_typing_code_no_wordlist,
                             outputs=[do_nameplate])
    S2_typing_code_no_wordlist.upon(got_wordlist,
                                    enter=S3_typing_code_yes_wordlist,
                                    outputs=[do_wordlist])
    S2_typing_code_no_wordlist.upon(choose_words, enter=S4_done,
                                    outputs=[do_words])
    S2_typing_code_no_wordlist.upon(got_nameplates,
                                    enter=S2_typing_code_no_wordlist, outputs=[])
    S3_typing_code_yes_wordlist.upon(choose_words, enter=S4_done,
                                     outputs=[do_words])
    S3_typing_code_yes_wordlist.upon(got_nameplates,
                                     enter=S3_typing_code_yes_wordlist,
                                     outputs=[])
    S4_done.upon(got_nameplates, enter=S4_done, outputs=[])
    S4_done.upon(got_wordlist, enter=S4_done, outputs=[])

    # methods for the CodeInputHelper to use
    #refresh_nameplates/_choose_nameplate/choose_words: @m.input methods

    def get_nameplate_completions(self, prefix):
        lp = len(prefix)
        completions = []
        for nameplate in self._nameplates:
            if nameplate.startswith(prefix):
                completions.append(nameplate[lp:])
        return completions

    def get_word_completions(self, prefix):
        if self._wordlist:
            return self._wordlist.get_completions(prefix)
        return []
