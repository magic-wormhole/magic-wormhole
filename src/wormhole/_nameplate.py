from __future__ import absolute_import, print_function, unicode_literals

import re

from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces
from ._wordlist import PGPWordList
from .errors import KeyFormatError


def validate_nameplate(nameplate):
    if not re.search(r'^\d+$', nameplate):
        raise KeyFormatError(
            "Nameplate '%s' must be numeric, with no spaces." % nameplate)


@implementer(_interfaces.INameplate)
class Nameplate(object):
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __init__(self):
        self._nameplate = None

    def wire(self, mailbox, input, rendezvous_connector, terminator):
        self._M = _interfaces.IMailbox(mailbox)
        self._I = _interfaces.IInput(input)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._T = _interfaces.ITerminator(terminator)

    # all -A states: not connected
    # all -B states: yes connected
    # B states serialize as A, so they deserialize as unconnected

    # S0: know nothing
    @m.state(initial=True)
    def S0A(self):
        pass  # pragma: no cover

    @m.state()
    def S0B(self):
        pass  # pragma: no cover

    # S1: nameplate known, never claimed
    @m.state()
    def S1A(self):
        pass  # pragma: no cover

    # S2: nameplate known, maybe claimed
    @m.state()
    def S2A(self):
        pass  # pragma: no cover

    @m.state()
    def S2B(self):
        pass  # pragma: no cover

    # S3: nameplate claimed
    @m.state()
    def S3A(self):
        pass  # pragma: no cover

    @m.state()
    def S3B(self):
        pass  # pragma: no cover

    # S4: maybe released
    @m.state()
    def S4A(self):
        pass  # pragma: no cover

    @m.state()
    def S4B(self):
        pass  # pragma: no cover

    # S5: released
    # we no longer care whether we're connected or not
    # @m.state()
    # def S5A(self): pass
    # @m.state()
    # def S5B(self): pass
    @m.state()
    def S5(self):
        pass  # pragma: no cover

    S5A = S5
    S5B = S5

    # from Boss
    def set_nameplate(self, nameplate):
        validate_nameplate(nameplate)  # can raise KeyFormatError
        self._set_nameplate(nameplate)

    @m.input()
    def _set_nameplate(self, nameplate):
        pass

    # from Mailbox
    @m.input()
    def release(self):
        pass

    # from Terminator
    @m.input()
    def close(self):
        pass

    # from RendezvousConnector
    @m.input()
    def connected(self):
        pass

    @m.input()
    def lost(self):
        pass

    @m.input()
    def rx_claimed(self, mailbox):
        pass

    @m.input()
    def rx_released(self):
        pass

    @m.output()
    def record_nameplate(self, nameplate):
        validate_nameplate(nameplate)
        self._nameplate = nameplate

    @m.output()
    def record_nameplate_and_RC_tx_claim(self, nameplate):
        validate_nameplate(nameplate)
        self._nameplate = nameplate
        self._RC.tx_claim(self._nameplate)

    @m.output()
    def RC_tx_claim(self):
        # when invoked via M.connected(), we must use the stored nameplate
        self._RC.tx_claim(self._nameplate)

    @m.output()
    def I_got_wordlist(self, mailbox):
        # TODO select wordlist based on nameplate properties, in rx_claimed
        wordlist = PGPWordList()
        self._I.got_wordlist(wordlist)

    @m.output()
    def M_got_mailbox(self, mailbox):
        self._M.got_mailbox(mailbox)

    @m.output()
    def RC_tx_release(self):
        assert self._nameplate
        self._RC.tx_release(self._nameplate)

    @m.output()
    def T_nameplate_done(self):
        self._T.nameplate_done()

    S0A.upon(_set_nameplate, enter=S1A, outputs=[record_nameplate])
    S0A.upon(connected, enter=S0B, outputs=[])
    S0A.upon(close, enter=S5A, outputs=[T_nameplate_done])
    S0B.upon(
        _set_nameplate, enter=S2B, outputs=[record_nameplate_and_RC_tx_claim])
    S0B.upon(lost, enter=S0A, outputs=[])
    S0B.upon(close, enter=S5A, outputs=[T_nameplate_done])

    S1A.upon(connected, enter=S2B, outputs=[RC_tx_claim])
    S1A.upon(close, enter=S5A, outputs=[T_nameplate_done])

    S2A.upon(connected, enter=S2B, outputs=[RC_tx_claim])
    S2A.upon(close, enter=S4A, outputs=[])
    S2B.upon(lost, enter=S2A, outputs=[])
    S2B.upon(rx_claimed, enter=S3B, outputs=[I_got_wordlist, M_got_mailbox])
    S2B.upon(close, enter=S4B, outputs=[RC_tx_release])

    S3A.upon(connected, enter=S3B, outputs=[])
    S3A.upon(close, enter=S4A, outputs=[])
    S3B.upon(lost, enter=S3A, outputs=[])
    # S3B.upon(rx_claimed, enter=S3B, outputs=[]) # shouldn't happen
    S3B.upon(release, enter=S4B, outputs=[RC_tx_release])
    S3B.upon(close, enter=S4B, outputs=[RC_tx_release])

    S4A.upon(connected, enter=S4B, outputs=[RC_tx_release])
    S4A.upon(close, enter=S4A, outputs=[])
    S4B.upon(lost, enter=S4A, outputs=[])
    S4B.upon(rx_claimed, enter=S4B, outputs=[])
    S4B.upon(rx_released, enter=S5B, outputs=[T_nameplate_done])
    S4B.upon(release, enter=S4B, outputs=[])  # mailbox is lazy
    # Mailbox doesn't remember how many times it's sent a release, and will
    # re-send a new one for each peer message it receives. Ignoring it here
    # is easier than adding a new pair of states to Mailbox.
    S4B.upon(close, enter=S4B, outputs=[])

    S5A.upon(connected, enter=S5B, outputs=[])
    S5B.upon(lost, enter=S5A, outputs=[])
    S5.upon(release, enter=S5, outputs=[])  # mailbox is lazy
    S5.upon(close, enter=S5, outputs=[])
