from __future__ import print_function, absolute_import, unicode_literals
from zope.interface import implementer
from attr import attrs, attrib
from attr.validators import provides
from automat import MethodicalMachine
from . import _interfaces
from types import FunctionType
from twisted.internet.defer import maybeDeferred


@attrs
@implementer(_interfaces.ICode)
class Mitigation(object):
    m = MethodicalMachine()
    acquire_token = attrib(validator=lambda i, a, v: callable(v))

    # XXX fixme?
    def wire(self, boss, rc):
        self._B = _interfaces.IBoss(boss)
        self._RC = _interfaces.IRendezvousConnector(rc)

    @m.state(initial=True)
    def idle(self):
        pass # pragma: no cover

    @m.state()
    def await_token(self):
        pass # pragma: no cover

    @m.input()
    def get_token(self):
        pass

    @m.input()
    def error(self, f):
        pass

    @m.input()
    def got_token(self, token):
        pass

    @m.input()
    def no_token_needed(self):
        pass

    @m.output()
    def _check_dos(self):
        if self._B._do_dos_mitigation:
            d = maybeDeferred(self.acquire_token)
            d.addCallback(self.got_token)
            d.addErrback(self.error)
        else:
            self.no_token_needed()

    @m.output()
    def _send_token(self, token):
        self._RC._tx("submit-permission", token=token)

    @m.output()
    def _send_error(self, f):
        self._B.error(f)

    @m.output()
    def _done(self):
        self._RC.token_accepted()

    idle.upon(
        get_token,
        enter=await_token,
        outputs=[_check_dos],
    )

    await_token.upon(
        got_token,
        enter=idle,
        outputs=[_send_token],
    )
    await_token.upon(
        no_token_needed,
        enter=idle,
        outputs=[_done],
    )
    await_token.upon(
        error,
        enter=idle,
        outputs=[_send_error],
    )
