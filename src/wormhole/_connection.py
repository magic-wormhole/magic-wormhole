
from six.moves.urllib_parse import urlparse
from attr import attrs, attrib
from twisted.internet import defer, endpoints #, error
from twisted.application import internet
from autobahn.twisted import websocket
from automat import MethodicalMachine

class WSClient(websocket.WebSocketClientProtocol):
    def onConnect(self, response):
        # this fires during WebSocket negotiation, and isn't very useful
        # unless you want to modify the protocol settings
        print("onConnect", response)
        #self.connection_machine.onConnect(self)

    def onOpen(self, *args):
        # this fires when the WebSocket is ready to go. No arguments
        print("onOpen", args)
        #self.wormhole_open = True
        # send BIND, since the MailboxMachine does not
        self.connection_machine.protocol_onOpen(self)
        #self.factory.d.callback(self)

    def onMessage(self, payload, isBinary):
        print("onMessage")
        return
        assert not isBinary
        self.wormhole._ws_dispatch_response(payload)

    def onClose(self, wasClean, code, reason):
        print("onClose")
        self.connection_machine.protocol_onClose(wasClean, code, reason)
        #if self.wormhole_open:
        #    self.wormhole._ws_closed(wasClean, code, reason)
        #else:
        #    # we closed before establishing a connection (onConnect) or
        #    # finishing WebSocket negotiation (onOpen): errback
        #    self.factory.d.errback(error.ConnectError(reason))

class WSFactory(websocket.WebSocketClientFactory):
    protocol = WSClient
    def buildProtocol(self, addr):
        proto = websocket.WebSocketClientFactory.buildProtocol(self, addr)
        proto.connection_machine = self.connection_machine
        #proto.wormhole_open = False
        return proto

# pip install (path to automat checkout)[visualize]
# automat-visualize wormhole._connection

class IRendezvousClient(Interface):
    # must be an IService too
    def set_dispatch(dispatcher):
        """Assign a dispatcher object to this client. The following methods
        will be called on this object when things happen:
        * rx_welcome(welcome -> dict)
        * rx_nameplates(nameplates -> list) # [{id: str,..}, ..]
        * rx_allocated(nameplate -> str)
        * rx_claimed(mailbox -> str)
        * rx_released()
        * rx_message(side -> str, phase -> str, body -> str, msg_id -> str)
        * rx_closed()
        * rx_pong(pong -> int)
        """
        pass
    def tx_list(): pass
    def tx_allocate(): pass
    def tx_claim(nameplate): pass
    def tx_release(): pass
    def tx_open(mailbox): pass
    def tx_add(phase, body): pass
    def tx_close(mood): pass
    def tx_ping(ping): pass

# We have one WSRelayClient for each wsurl we know about, and it lasts
# as long as its parent Wormhole does.

@attrs
class WSRelayClient(service.MultiService, object):
    _journal = attrib()
    _wormhole = attrib()
    _mailbox = attrib()
    _ws_url = attrib()
    _reactor = attrib()

    def __init__(self):
        f = WSFactory(self._ws_url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        f.connection_machine = self # calls onOpen and onClose
        p = urlparse(self._ws_url)
        ep = self._make_endpoint(p.hostname, p.port or 80)
        # default policy: 1s initial, random exponential backoff, max 60s
        self._client_service = internet.ClientService(ep, f)
        self._connector = None
        self._done_d = defer.Deferred()
        self._current_delay = self.INITIAL_DELAY

    def _make_endpoint(self, hostname, port):
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    # inputs from elsewhere
    def d_callback(self, p):
        self._p = p
        self._m.d_callback()
    def d_errback(self, f):
        self._f = f
        self._m.d_errback()
    def protocol_onOpen(self, p):
        self._m.onOpen()
    def protocol_onClose(self, wasClean, code, reason):
        self._m.onClose()
    def C_stop(self):
        self._m.stop()
    def timer_expired(self):
        self._m.expire()

    # outputs driven by the state machine
    def ep_connect(self):
        print("ep_connect()")
        self._d = self._ep.connect(self._f)
        self._d.addCallbacks(self.d_callback, self.d_errback)
    def connection_established(self):
        self._connection = WSConnection(ws, self._wormhole.appid,
                                        self._wormhole.side, self)
        self._mailbox.connected(ws)
        self._wormhole.add_connection(self._connection)
        self._ws_send_command("bind", appid=self._appid, side=self._side)
    def M_lost(self):
        self._wormhole.M_lost(self._connection)
        self._connection = None
    def start_timer(self):
        print("start_timer")
        self._t = self._reactor.callLater(3.0, self.expire)
    def cancel_timer(self):
        print("cancel_timer")
        self._t.cancel()
        self._t = None
    def dropConnection(self):
        print("dropConnection")
        self._ws.dropConnection()
    def notify_fail(self):
        print("notify_fail", self._f.value if self._f else None)
        self._done_d.errback(self._f)
    def MC_stopped(self):
        pass


def tryit(reactor):
    cm = WSRelayClient(None, "ws://127.0.0.1:4000/v1", reactor)
    print("_ConnectionMachine created")
    print("start:", cm.start())
    print("waiting on _done_d to finish")
    return cm._done_d

# http://autobahn-python.readthedocs.io/en/latest/websocket/programming.html
# observed sequence of events:
# success: d_callback, onConnect(response), onOpen(), onMessage()
# negotifail (non-websocket): d_callback, onClose()
# noconnect: d_errback

def tryws(reactor):
    ws_url = "ws://127.0.0.1:40001/v1"
    f = WSFactory(ws_url)
    p = urlparse(ws_url)
    ep = endpoints.HostnameEndpoint(reactor, p.hostname, p.port or 80)
    d = ep.connect(f)
    def _good(p): print("_good", p)
    def _bad(f): print("_bad", f)
    d.addCallbacks(_good, _bad)
    return defer.Deferred()

if __name__ == "__main__":
    import sys
    from twisted.python import log
    log.startLogging(sys.stdout)
    from twisted.internet.task import react
    react(tryit)

# ??? a new WSConnection is created each time the WSRelayClient gets through
# negotiation
