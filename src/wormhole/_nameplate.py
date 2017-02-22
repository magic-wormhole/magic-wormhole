from zope.interface import implementer
from automat import MethodicalMachine
from . import _interfaces

@implementer(_interfaces.INameplateLister)
class NameplateListing(object):
    m = MethodicalMachine()
    def __init__(self):
        pass
    def wire(self, rendezvous_connector, code):
        self._RC = _interfaces.IRendezvousConnector(rendezvous_connector)
        self._C = _interfaces.ICode(code)

    # Ideally, each API request would spawn a new "list_nameplates" message
    # to the server, so the response would be maximally fresh, but that would
    # require correlating server request+response messages, and the protocol
    # is intended to be less stateful than that. So we offer a weaker
    # freshness property: if no server requests are in flight, then a new API
    # request will provoke a new server request, and the result will be
    # fresh. But if a server request is already in flight when a second API
    # request arrives, both requests will be satisfied by the same response.

    @m.state(initial=True)
    def S0A_idle_disconnected(self): pass
    @m.state()
    def S1A_wanting_disconnected(self): pass
    @m.state()
    def S0B_idle_connected(self): pass
    @m.state()
    def S1B_wanting_connected(self): pass

    @m.input()
    def connected(self): pass
    @m.input()
    def lost(self): pass
    @m.input()
    def refresh_nameplates(self): pass
    @m.input()
    def rx_nameplates(self, message): pass

    @m.output()
    def RC_tx_list(self):
        self._RC.tx_list()
    @m.output()
    def C_got_nameplates(self, message):
        self._C.got_nameplates(message["nameplates"])

    S0A_idle_disconnected.upon(connected, enter=S0B_idle_connected, outputs=[])
    S0B_idle_connected.upon(lost, enter=S0A_idle_disconnected, outputs=[])

    S0A_idle_disconnected.upon(refresh_nameplates,
                               enter=S1A_wanting_disconnected, outputs=[])
    S1A_wanting_disconnected.upon(refresh_nameplates,
                                  enter=S1A_wanting_disconnected, outputs=[])
    S1A_wanting_disconnected.upon(connected, enter=S1B_wanting_connected,
                                  outputs=[RC_tx_list])
    S0B_idle_connected.upon(refresh_nameplates, enter=S1B_wanting_connected,
                            outputs=[RC_tx_list])
    S0B_idle_connected.upon(rx_nameplates, enter=S0B_idle_connected,
                            outputs=[C_got_nameplates])
    S1B_wanting_connected.upon(lost, enter=S1A_wanting_disconnected, outputs=[])
    S1B_wanting_connected.upon(refresh_nameplates, enter=S1B_wanting_connected,
                               outputs=[RC_tx_list])
    S1B_wanting_connected.upon(rx_nameplates, enter=S0B_idle_connected,
                               outputs=[C_got_nameplates])
