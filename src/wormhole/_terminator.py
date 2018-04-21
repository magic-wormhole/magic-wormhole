from __future__ import absolute_import, print_function, unicode_literals

from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces


@implementer(_interfaces.ITerminator)
class Terminator(object):
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def __init__(self):
        self._mood = None

    def wire(self, boss, rendezvous_connector, nameplate, mailbox):
        self._B = _interfaces.IBoss(boss)
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._N = _interfaces.INameplate(nameplate)
        self._M = _interfaces.IMailbox(mailbox)

    # 4*2-1 main states:
    # (nm, m, n, 0): nameplate and/or mailbox is active
    # (o, ""): open (not-yet-closing), or trying to close
    # S0 is special: we don't hang out in it

    # TODO: rename o to 0, "" to 1. "S1" is special/terminal
    # so S0nm/S0n/S0m/S0, S1nm/S1n/S1m/(S1)

    # We start in Snmo (non-closing). When both nameplate and mailboxes are
    # done, and we're closing, then we stop the RendezvousConnector

    @m.state(initial=True)
    def Snmo(self):
        pass  # pragma: no cover

    @m.state()
    def Smo(self):
        pass  # pragma: no cover

    @m.state()
    def Sno(self):
        pass  # pragma: no cover

    @m.state()
    def S0o(self):
        pass  # pragma: no cover

    @m.state()
    def Snm(self):
        pass  # pragma: no cover

    @m.state()
    def Sm(self):
        pass  # pragma: no cover

    @m.state()
    def Sn(self):
        pass  # pragma: no cover

    # @m.state()
    # def S0(self): pass # unused

    @m.state()
    def S_stopping(self):
        pass  # pragma: no cover

    @m.state()
    def S_stopped(self, terminal=True):
        pass  # pragma: no cover

    # from Boss
    @m.input()
    def close(self, mood):
        pass

    # from Nameplate
    @m.input()
    def nameplate_done(self):
        pass

    # from Mailbox
    @m.input()
    def mailbox_done(self):
        pass

    # from RendezvousConnector
    @m.input()
    def stopped(self):
        pass

    @m.output()
    def close_nameplate(self, mood):
        self._N.close()  # ignores mood

    @m.output()
    def close_mailbox(self, mood):
        self._M.close(mood)

    @m.output()
    def ignore_mood_and_RC_stop(self, mood):
        self._RC.stop()

    @m.output()
    def RC_stop(self):
        self._RC.stop()

    @m.output()
    def B_closed(self):
        self._B.closed()

    Snmo.upon(mailbox_done, enter=Sno, outputs=[])
    Snmo.upon(close, enter=Snm, outputs=[close_nameplate, close_mailbox])
    Snmo.upon(nameplate_done, enter=Smo, outputs=[])

    Sno.upon(close, enter=Sn, outputs=[close_nameplate, close_mailbox])
    Sno.upon(nameplate_done, enter=S0o, outputs=[])

    Smo.upon(close, enter=Sm, outputs=[close_nameplate, close_mailbox])
    Smo.upon(mailbox_done, enter=S0o, outputs=[])

    Snm.upon(mailbox_done, enter=Sn, outputs=[])
    Snm.upon(nameplate_done, enter=Sm, outputs=[])

    Sn.upon(nameplate_done, enter=S_stopping, outputs=[RC_stop])
    S0o.upon(
        close,
        enter=S_stopping,
        outputs=[close_nameplate, close_mailbox, ignore_mood_and_RC_stop])
    Sm.upon(mailbox_done, enter=S_stopping, outputs=[RC_stop])

    S_stopping.upon(stopped, enter=S_stopped, outputs=[B_closed])
