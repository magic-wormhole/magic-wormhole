from __future__ import absolute_import, print_function, unicode_literals

# We use 'threading' defensively here, to detect if we're being called from a
# non-main thread. _rlcompleter.py is the only internal Wormhole code that
# deliberately creates a new thread.
import threading

from attr import attrib, attrs
from attr.validators import provides
from automat import MethodicalMachine
from twisted.internet import defer
from zope.interface import implementer

from . import _interfaces, errors
from ._nameplate import validate_nameplate


def first(outputs):
    return list(outputs)[0]


@attrs
@implementer(_interfaces.IInput)
class Input(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._all_nameplates = set()
        self._nameplate = None
        self._wordlist = None
        self._wordlist_waiters = []
        self._trace = None

    def set_debug(self, f):
        self._trace = f

    def _debug(self, what):  # pragma: no cover
        if self._trace:
            self._trace(old_state="", input=what, new_state="")

    def wire(self, code, lister):
        self._C = _interfaces.ICode(code)
        self._L = _interfaces.ILister(lister)

    def when_wordlist_is_available(self):
        if self._wordlist:
            return defer.succeed(None)
        d = defer.Deferred()
        self._wordlist_waiters.append(d)
        return d

    @m.state(initial=True)
    def S0_idle(self):
        pass  # pragma: no cover

    @m.state()
    def S1_typing_nameplate(self):
        pass  # pragma: no cover

    @m.state()
    def S2_typing_code_no_wordlist(self):
        pass  # pragma: no cover

    @m.state()
    def S3_typing_code_yes_wordlist(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def S4_done(self):
        pass  # pragma: no cover

    # from Code
    @m.input()
    def start(self):
        pass

    # from Lister
    @m.input()
    def got_nameplates(self, all_nameplates):
        pass

    # from Nameplate
    @m.input()
    def got_wordlist(self, wordlist):
        pass

    # API provided to app as ICodeInputHelper
    @m.input()
    def refresh_nameplates(self):
        pass

    @m.input()
    def get_nameplate_completions(self, prefix):
        pass

    def choose_nameplate(self, nameplate):
        validate_nameplate(nameplate)  # can raise KeyFormatError
        self._choose_nameplate(nameplate)

    @m.input()
    def _choose_nameplate(self, nameplate):
        pass

    @m.input()
    def get_word_completions(self, prefix):
        pass

    @m.input()
    def choose_words(self, words):
        pass

    @m.output()
    def do_start(self):
        self._start_timing = self._timing.add("input code", waiting="user")
        self._L.refresh()
        return Helper(self)

    @m.output()
    def do_refresh(self):
        self._L.refresh()

    @m.output()
    def record_nameplates(self, all_nameplates):
        # we get a set of nameplate id strings
        self._all_nameplates = all_nameplates

    @m.output()
    def _get_nameplate_completions(self, prefix):
        completions = set()
        for nameplate in self._all_nameplates:
            if nameplate.startswith(prefix):
                # TODO: it's a little weird that Input is responsible for the
                # hyphen on nameplates, but WordList owns it for words
                completions.add(nameplate + "-")
        return completions

    @m.output()
    def record_all_nameplates(self, nameplate):
        self._nameplate = nameplate
        self._C.got_nameplate(nameplate)

    @m.output()
    def record_wordlist(self, wordlist):
        from ._rlcompleter import debug
        debug("  -record_wordlist")
        self._wordlist = wordlist

    @m.output()
    def notify_wordlist_waiters(self, wordlist):
        while self._wordlist_waiters:
            d = self._wordlist_waiters.pop()
            d.callback(None)

    @m.output()
    def no_word_completions(self, prefix):
        return set()

    @m.output()
    def _get_word_completions(self, prefix):
        assert self._wordlist
        return self._wordlist.get_completions(prefix)

    @m.output()
    def raise_must_choose_nameplate1(self, prefix):
        raise errors.MustChooseNameplateFirstError()

    @m.output()
    def raise_must_choose_nameplate2(self, words):
        raise errors.MustChooseNameplateFirstError()

    @m.output()
    def raise_already_chose_nameplate1(self):
        raise errors.AlreadyChoseNameplateError()

    @m.output()
    def raise_already_chose_nameplate2(self, prefix):
        raise errors.AlreadyChoseNameplateError()

    @m.output()
    def raise_already_chose_nameplate3(self, nameplate):
        raise errors.AlreadyChoseNameplateError()

    @m.output()
    def raise_already_chose_words1(self, prefix):
        raise errors.AlreadyChoseWordsError()

    @m.output()
    def raise_already_chose_words2(self, words):
        raise errors.AlreadyChoseWordsError()

    @m.output()
    def do_words(self, words):
        code = self._nameplate + "-" + words
        self._start_timing.finish()
        self._C.finished_input(code)

    S0_idle.upon(
        start, enter=S1_typing_nameplate, outputs=[do_start], collector=first)
    # wormholes that don't use input_code (i.e. they use allocate_code or
    # generate_code) will never start() us, but Nameplate will give us a
    # wordlist anyways (as soon as the nameplate is claimed), so handle it.
    S0_idle.upon(
        got_wordlist,
        enter=S0_idle,
        outputs=[record_wordlist, notify_wordlist_waiters])
    S1_typing_nameplate.upon(
        got_nameplates, enter=S1_typing_nameplate, outputs=[record_nameplates])
    # but wormholes that *do* use input_code should not get got_wordlist
    # until after we tell Code that we got_nameplate, which is the earliest
    # it can be claimed
    S1_typing_nameplate.upon(
        refresh_nameplates, enter=S1_typing_nameplate, outputs=[do_refresh])
    S1_typing_nameplate.upon(
        get_nameplate_completions,
        enter=S1_typing_nameplate,
        outputs=[_get_nameplate_completions],
        collector=first)
    S1_typing_nameplate.upon(
        _choose_nameplate,
        enter=S2_typing_code_no_wordlist,
        outputs=[record_all_nameplates])
    S1_typing_nameplate.upon(
        get_word_completions,
        enter=S1_typing_nameplate,
        outputs=[raise_must_choose_nameplate1])
    S1_typing_nameplate.upon(
        choose_words,
        enter=S1_typing_nameplate,
        outputs=[raise_must_choose_nameplate2])

    S2_typing_code_no_wordlist.upon(
        got_nameplates, enter=S2_typing_code_no_wordlist, outputs=[])
    S2_typing_code_no_wordlist.upon(
        got_wordlist,
        enter=S3_typing_code_yes_wordlist,
        outputs=[record_wordlist, notify_wordlist_waiters])
    S2_typing_code_no_wordlist.upon(
        refresh_nameplates,
        enter=S2_typing_code_no_wordlist,
        outputs=[raise_already_chose_nameplate1])
    S2_typing_code_no_wordlist.upon(
        get_nameplate_completions,
        enter=S2_typing_code_no_wordlist,
        outputs=[raise_already_chose_nameplate2])
    S2_typing_code_no_wordlist.upon(
        _choose_nameplate,
        enter=S2_typing_code_no_wordlist,
        outputs=[raise_already_chose_nameplate3])
    S2_typing_code_no_wordlist.upon(
        get_word_completions,
        enter=S2_typing_code_no_wordlist,
        outputs=[no_word_completions],
        collector=first)
    S2_typing_code_no_wordlist.upon(
        choose_words, enter=S4_done, outputs=[do_words])

    S3_typing_code_yes_wordlist.upon(
        got_nameplates, enter=S3_typing_code_yes_wordlist, outputs=[])
    # got_wordlist: should never happen
    S3_typing_code_yes_wordlist.upon(
        refresh_nameplates,
        enter=S3_typing_code_yes_wordlist,
        outputs=[raise_already_chose_nameplate1])
    S3_typing_code_yes_wordlist.upon(
        get_nameplate_completions,
        enter=S3_typing_code_yes_wordlist,
        outputs=[raise_already_chose_nameplate2])
    S3_typing_code_yes_wordlist.upon(
        _choose_nameplate,
        enter=S3_typing_code_yes_wordlist,
        outputs=[raise_already_chose_nameplate3])
    S3_typing_code_yes_wordlist.upon(
        get_word_completions,
        enter=S3_typing_code_yes_wordlist,
        outputs=[_get_word_completions],
        collector=first)
    S3_typing_code_yes_wordlist.upon(
        choose_words, enter=S4_done, outputs=[do_words])

    S4_done.upon(got_nameplates, enter=S4_done, outputs=[])
    S4_done.upon(got_wordlist, enter=S4_done, outputs=[])
    S4_done.upon(
        refresh_nameplates,
        enter=S4_done,
        outputs=[raise_already_chose_nameplate1])
    S4_done.upon(
        get_nameplate_completions,
        enter=S4_done,
        outputs=[raise_already_chose_nameplate2])
    S4_done.upon(
        _choose_nameplate,
        enter=S4_done,
        outputs=[raise_already_chose_nameplate3])
    S4_done.upon(
        get_word_completions,
        enter=S4_done,
        outputs=[raise_already_chose_words1])
    S4_done.upon(
        choose_words, enter=S4_done, outputs=[raise_already_chose_words2])


# we only expose the Helper to application code, not _Input
@attrs
@implementer(_interfaces.IInputHelper)
class Helper(object):
    _input = attrib()

    def __attrs_post_init__(self):
        self._main_thread = threading.current_thread().ident

    def refresh_nameplates(self):
        assert threading.current_thread().ident == self._main_thread
        self._input.refresh_nameplates()

    def get_nameplate_completions(self, prefix):
        assert threading.current_thread().ident == self._main_thread
        return self._input.get_nameplate_completions(prefix)

    def choose_nameplate(self, nameplate):
        assert threading.current_thread().ident == self._main_thread
        self._input._debug("I.choose_nameplate")
        self._input.choose_nameplate(nameplate)
        self._input._debug("I.choose_nameplate finished")

    def when_wordlist_is_available(self):
        assert threading.current_thread().ident == self._main_thread
        return self._input.when_wordlist_is_available()

    def get_word_completions(self, prefix):
        assert threading.current_thread().ident == self._main_thread
        return self._input.get_word_completions(prefix)

    def choose_words(self, words):
        assert threading.current_thread().ident == self._main_thread
        self._input._debug("I.choose_words")
        self._input.choose_words(words)
        self._input._debug("I.choose_words finished")
