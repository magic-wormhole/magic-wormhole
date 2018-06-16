from __future__ import absolute_import, print_function, unicode_literals

from attr import attrib, attrs
from attr.validators import provides
from automat import MethodicalMachine
from zope.interface import implementer

from . import _interfaces


@attrs
@implementer(_interfaces.ILister)
class Lister(object):
    _timing = attrib(validator=provides(_interfaces.ITiming))
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace",
                        lambda self, f: None)  # pragma: no cover

    def wire(self, rendezvous_connector, input):
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._I = _interfaces.IInput(input)

    # Ideally, each API request would spawn a new "list_nameplates" message
    # to the server, so the response would be maximally fresh, but that would
    # require correlating server request+response messages, and the protocol
    # is intended to be less stateful than that. So we offer a weaker
    # freshness property: if no server requests are in flight, then a new API
    # request will provoke a new server request, and the result will be
    # fresh. But if a server request is already in flight when a second API
    # request arrives, both requests will be satisfied by the same response.

    @m.state(initial=True)
    def S0A_idle_disconnected(self):
        pass  # pragma: no cover

    @m.state()
    def S1A_wanting_disconnected(self):
        pass  # pragma: no cover

    @m.state()
    def S0B_idle_connected(self):
        pass  # pragma: no cover

    @m.state()
    def S1B_wanting_connected(self):
        pass  # pragma: no cover

    @m.input()
    def connected(self):
        pass

    @m.input()
    def lost(self):
        pass

    @m.input()
    def refresh(self):
        pass

    @m.input()
    def rx_nameplates(self, all_nameplates):
        pass

    @m.output()
    def RC_tx_list(self):
        self._RC.tx_list()

    @m.output()
    def I_got_nameplates(self, all_nameplates):
        # We get a set of nameplate ids. There may be more attributes in the
        # future: change RendezvousConnector._response_handle_nameplates to
        # get them
        self._I.got_nameplates(all_nameplates)

    S0A_idle_disconnected.upon(connected, enter=S0B_idle_connected, outputs=[])
    S0B_idle_connected.upon(lost, enter=S0A_idle_disconnected, outputs=[])

    S0A_idle_disconnected.upon(
        refresh, enter=S1A_wanting_disconnected, outputs=[])
    S1A_wanting_disconnected.upon(
        refresh, enter=S1A_wanting_disconnected, outputs=[])
    S1A_wanting_disconnected.upon(
        connected, enter=S1B_wanting_connected, outputs=[RC_tx_list])
    S0B_idle_connected.upon(
        refresh, enter=S1B_wanting_connected, outputs=[RC_tx_list])
    S0B_idle_connected.upon(
        rx_nameplates, enter=S0B_idle_connected, outputs=[I_got_nameplates])
    S1B_wanting_connected.upon(
        lost, enter=S1A_wanting_disconnected, outputs=[])
    S1B_wanting_connected.upon(
        refresh, enter=S1B_wanting_connected, outputs=[RC_tx_list])
    S1B_wanting_connected.upon(
        rx_nameplates, enter=S0B_idle_connected, outputs=[I_got_nameplates])
