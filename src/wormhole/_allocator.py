from __future__ import absolute_import, print_function, unicode_literals

from attr import attrib, attrs
from attr.validators import provides
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces


@attrs
@implementer(_interfaces.IAllocator)
class Allocator(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def wire(self, rendezvous_connector, code):
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._C = _interfaces.ICode(code)

    @m.state(initial=True)
    def S0A_idle(self):
        pass  # pragma: no cover

    @m.state()
    def S0B_idle_connected(self):
        pass  # pragma: no cover

    @m.state()
    def S1A_allocating(self):
        pass  # pragma: no cover

    @m.state()
    def S1B_allocating_connected(self):
        pass  # pragma: no cover

    @m.state()
    def S2_done(self):
        pass  # pragma: no cover

    # from Code
    @m.input()
    def allocate(self, length, wordlist):
        pass

    # from RendezvousConnector
    @m.input()
    def connected(self):
        pass

    @m.input()
    def lost(self):
        pass

    @m.input()
    def rx_allocated(self, nameplate):
        pass

    @m.output()
    def stash(self, length, wordlist):
        self._length = length
        self._wordlist = _interfaces.IWordlist(wordlist)

    @m.output()
    def stash_and_RC_rx_allocate(self, length, wordlist):
        self._length = length
        self._wordlist = _interfaces.IWordlist(wordlist)
        self._RC.tx_allocate()

    @m.output()
    def RC_tx_allocate(self):
        self._RC.tx_allocate()

    @m.output()
    def build_and_notify(self, nameplate):
        words = self._wordlist.choose_words(self._length)
        code = nameplate + "-" + words
        self._C.allocated(nameplate, code)

    S0A_idle.upon(connected, enter=S0B_idle_connected, outputs=[])
    S0B_idle_connected.upon(lost, enter=S0A_idle, outputs=[])

    S0A_idle.upon(allocate, enter=S1A_allocating, outputs=[stash])
    S0B_idle_connected.upon(
        allocate,
        enter=S1B_allocating_connected,
        outputs=[stash_and_RC_rx_allocate])

    S1A_allocating.upon(
        connected, enter=S1B_allocating_connected, outputs=[RC_tx_allocate])
    S1B_allocating_connected.upon(lost, enter=S1A_allocating, outputs=[])

    S1B_allocating_connected.upon(
        rx_allocated, enter=S2_done, outputs=[build_and_notify])

    S2_done.upon(connected, enter=S2_done, outputs=[])
    S2_done.upon(lost, enter=S2_done, outputs=[])
