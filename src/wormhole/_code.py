from __future__ import print_function, absolute_import, unicode_literals
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides
from automat import MethodicalMachine
from . import _interfaces
from ._nameplate import validate_nameplate
from .errors import KeyFormatError


def validate_code(code):
    if ' ' in code:
        raise KeyFormatError("Code '%s' contains spaces." % code)
    nameplate = code.split("-", 2)[0]
    validate_nameplate(nameplate)  # can raise KeyFormatError


def first(outputs):
    return list(outputs)[0]


@attrs
@implementer(_interfaces.ICode)
class Code(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def wire(self, boss, allocator, nameplate, key, input):
        self._B = _interfaces.IBoss(boss)
        self._A = _interfaces.IAllocator(allocator)
        self._N = _interfaces.INameplate(nameplate)
        self._K = _interfaces.IKey(key)
        self._I = _interfaces.IInput(input)

    @m.state(initial=True)
    def S0_idle(self):
        pass  # pragma: no cover

    @m.state()
    def S1_inputting_nameplate(self):
        pass  # pragma: no cover

    @m.state()
    def S2_inputting_words(self):
        pass  # pragma: no cover

    @m.state()
    def S3_allocating(self):
        pass  # pragma: no cover

    @m.state()
    def S4_known(self):
        pass  # pragma: no cover

    # from App
    @m.input()
    def allocate_code(self, length, wordlist):
        pass

    @m.input()
    def input_code(self):
        pass

    def set_code(self, code):
        validate_code(code)  # can raise KeyFormatError
        self._set_code(code)

    @m.input()
    def _set_code(self, code):
        pass

    # from Allocator
    @m.input()
    def allocated(self, nameplate, code):
        pass

    # from Input
    @m.input()
    def got_nameplate(self, nameplate):
        pass

    @m.input()
    def finished_input(self, code):
        pass

    @m.output()
    def do_set_code(self, code):
        nameplate = code.split("-", 2)[0]
        self._N.set_nameplate(nameplate)
        self._B.got_code(code)
        self._K.got_code(code)

    @m.output()
    def do_start_input(self):
        return self._I.start()

    @m.output()
    def do_middle_input(self, nameplate):
        self._N.set_nameplate(nameplate)

    @m.output()
    def do_finish_input(self, code):
        self._B.got_code(code)
        self._K.got_code(code)

    @m.output()
    def do_start_allocate(self, length, wordlist):
        self._A.allocate(length, wordlist)

    @m.output()
    def do_finish_allocate(self, nameplate, code):
        assert code.startswith(nameplate + "-"), (nameplate, code)
        self._N.set_nameplate(nameplate)
        self._B.got_code(code)
        self._K.got_code(code)

    S0_idle.upon(_set_code, enter=S4_known, outputs=[do_set_code])
    S0_idle.upon(
        input_code,
        enter=S1_inputting_nameplate,
        outputs=[do_start_input],
        collector=first)
    S1_inputting_nameplate.upon(
        got_nameplate, enter=S2_inputting_words, outputs=[do_middle_input])
    S2_inputting_words.upon(
        finished_input, enter=S4_known, outputs=[do_finish_input])
    S0_idle.upon(
        allocate_code, enter=S3_allocating, outputs=[do_start_allocate])
    S3_allocating.upon(allocated, enter=S4_known, outputs=[do_finish_allocate])
