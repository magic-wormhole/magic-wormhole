
from six.moves.urllib_parse import urlparse
from attr import attrs, attrib
from twisted.internet import protocol, reactor
from twisted.internet import defer, endpoints #, error
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
        self.connection_machine.onOpen(self)
        #self.factory.d.callback(self)

    def onMessage(self, payload, isBinary):
        print("onMessage")
        return
        assert not isBinary
        self.wormhole._ws_dispatch_response(payload)

    def onClose(self, wasClean, code, reason):
        print("onClose")
        self.connection_machine.onClose(f=None)
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


class Dummy(protocol.Protocol):
    def connectionMade(self):
        print("connectionMade")
        reactor.callLater(1.0, self.factory.cm.onConnect, "fake ws")
        reactor.callLater(2.0, self.transport.loseConnection)
    def connectionLost(self, why):
        self.factory.cm.onClose(why)

# pip install (path to automat checkout)[visualize]
# automat-visualize wormhole._connection

class WebSocketMachine(object):
    m = MethodicalMachine()
    ALLOW_CLOSE = True

    def __init__(self, ws_url, reactor):
        self._reactor = reactor
        self._f = f = WSFactory(ws_url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        f.connection_machine = self # calls onOpen and onClose
        #self._f = protocol.ClientFactory()
        #self._f.cm = self
        #self._f.protocol = Dummy
        p = urlparse(ws_url)
        self._ep = self._make_endpoint(p.hostname, p.port or 80)
        self._connector = None
        self._done_d = defer.Deferred()
    def _make_endpoint(self, hostname, port):
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    @m.state(initial=True)
    def initial(self): pass
    @m.state()
    def first_time_connecting(self): pass
    @m.state()
    def negotiating(self): pass
    @m.state(terminal=True)
    def failed(self): pass
    @m.state()
    def open(self): pass
    @m.state()
    def waiting(self): pass
    @m.state()
    def connecting(self): pass
    if ALLOW_CLOSE:
        @m.state()
        def disconnecting(self): pass
        @m.state()
        def disconnecting2(self): pass
        @m.state(terminal=True)
        def closed(self): pass


    @m.input()
    def start(self): pass ; print("in start")
    @m.input()
    def d_callback(self, p): pass ; print("in d_callback", p)
    @m.input()
    def d_errback(self, f): pass ; print("in d_errback", f)
    @m.input()
    def d_cancel(self): pass
    @m.input()
    def onOpen(self, ws): pass ; print("in onOpen")
    @m.input()
    def onClose(self, f): pass
    @m.input()
    def expire(self): pass
    if ALLOW_CLOSE:
        @m.input()
        def close(self): pass

    @m.output()
    def ep_connect(self):
        "ep.connect()"
        print("ep_connect()")
        self._d = self._ep.connect(self._f)
        self._d.addCallbacks(self.d_callback, self.d_errback)
    @m.output()
    def handle_connection(self, ws):
        print("handle_connection", ws)
        #self._wormhole.new_connection(Connection(ws))
    @m.output()
    def start_timer(self, f):
        print("start_timer")
        self._t = self._reactor.callLater(3.0, self.expire)
    @m.output()
    def cancel_timer(self):
        print("cancel_timer")
        self._t.cancel()
        self._t = None
    @m.output()
    def dropConnection(self):
        print("dropConnection")
        self._ws.dropConnection()
    @m.output()
    def notify_fail(self, f):
        print("notify_fail", f.value)
        self._done_d.errback(f)

    initial.upon(start, enter=first_time_connecting, outputs=[ep_connect])
    first_time_connecting.upon(d_callback, enter=negotiating, outputs=[])
    first_time_connecting.upon(d_errback, enter=failed, outputs=[notify_fail])
    first_time_connecting.upon(onClose, enter=failed, outputs=[notify_fail])
    if ALLOW_CLOSE:
        first_time_connecting.upon(close, enter=disconnecting2, outputs=[d_cancel])
        disconnecting2.upon(d_errback, enter=closed, outputs=[])

    negotiating.upon(onOpen, enter=open, outputs=[handle_connection])
    if ALLOW_CLOSE:
        negotiating.upon(close, enter=disconnecting, outputs=[dropConnection])
    negotiating.upon(onClose, enter=failed, outputs=[notify_fail])

    open.upon(onClose, enter=waiting, outputs=[start_timer])
    if ALLOW_CLOSE:
        open.upon(close, enter=disconnecting, outputs=[dropConnection])
    connecting.upon(d_callback, enter=negotiating, outputs=[])
    connecting.upon(d_errback, enter=waiting, outputs=[start_timer])
    connecting.upon(onClose, enter=waiting, outputs=[start_timer])
    if ALLOW_CLOSE:
        connecting.upon(close, enter=disconnecting2, outputs=[d_cancel])

    waiting.upon(expire, enter=connecting, outputs=[ep_connect])
    if ALLOW_CLOSE:
        waiting.upon(close, enter=closed, outputs=[cancel_timer])
        disconnecting.upon(onClose, enter=closed, outputs=[])

def tryit(reactor):
    cm = WebSocketMachine("ws://127.0.0.1:4000/v1", reactor)
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

@attrs
class Connection(object):
    _ws = attrib()
    _appid = attrib()
    _side = attrib()
    _ws_machine = attrib()
    m = MethodicalMachine()

    @m.state(initial=True)
    def unbound(self): pass
    @m.state()
    def binding(self): pass
    @m.state()
    def neither(self): pass
    @m.state()
    def has_nameplate(self): pass
    @m.state()
    def has_mailbox(self): pass
    @m.state()
    def has_both(self): pass
    @m.state()
    def closing(self): pass
    @m.state()
    def closed(self): pass

    @m.input()
    def bind(self): pass
    @m.input()
    def ack_bind(self): pass
    @m.input()
    def c_set_nameplate(self): pass
    @m.input()
    def c_set_mailbox(self, mailbox): pass
    @m.input()
    def c_remove_nameplate(self): pass
    @m.input()
    def c_remove_mailbox(self): pass
    @m.input()
    def ack_close(self): pass

    @m.output()
    def send_bind(self):
        self._ws_send_command("bind", appid=self._appid, side=self._side)
    @m.output()
    def notify_bound(self):
        self._nameplate_machine.bound()
    @m.output()
    def m_set_mailbox(self, mailbox):
        self._mailbox_machine.m_set_mailbox(mailbox)
    @m.output()
    def request_close(self):
        self._ws_machine.close()
    @m.output()
    def notify_close(self):
        pass

    unbound.upon(bind, enter=binding, outputs=[send_bind])
    binding.upon(ack_bind, enter=neither, outputs=[notify_bound])
    neither.upon(c_set_nameplate, enter=has_nameplate, outputs=[])
    neither.upon(c_set_mailbox, enter=has_mailbox, outputs=[m_set_mailbox])
    has_nameplate.upon(c_set_mailbox, enter=has_both, outputs=[m_set_mailbox])
    has_nameplate.upon(c_remove_nameplate, enter=closing, outputs=[request_close])
    has_mailbox.upon(c_set_nameplate, enter=has_both, outputs=[])
    has_mailbox.upon(c_remove_mailbox, enter=closing, outputs=[request_close])
    has_both.upon(c_remove_nameplate, enter=has_mailbox, outputs=[])
    has_both.upon(c_remove_mailbox, enter=has_nameplate, outputs=[])
    closing.upon(ack_close, enter=closed, outputs=[])

class NameplateMachine(object):
    m = MethodicalMachine()

    def bound(self):
        pass

    @m.state(initial=True)
    def unclaimed(self): pass # but bound
    @m.state()
    def claiming(self): pass
    @m.state()
    def claimed(self): pass
    @m.state()
    def releasing(self): pass

    @m.input()
    def list_nameplates(self): pass
    @m.input()
    def got_nameplates(self, nameplates): pass # response("nameplates")
    @m.input()
    def learned_nameplate(self, nameplate):
        """Call learned_nameplate() when you learn the nameplate: either
        through allocation or code entry"""
        pass
    @m.input()
    def claim_acked(self, mailbox): pass # response("claimed")
    @m.input()
    def release(self): pass
    @m.input()
    def release_acked(self): pass # response("released")

    @m.output()
    def send_list_nameplates(self):
        self._ws_send_command("list")
    @m.output()
    def notify_nameplates(self, nameplates):
        # tell somebody
        pass
    @m.output()
    def send_claim(self, nameplate):
        self._ws_send_command("claim", nameplate=nameplate)
    @m.output()
    def c_set_nameplate(self, mailbox):
        self._connection_machine.set_nameplate()
    @m.output()
    def c_set_mailbox(self, mailbox):
        self._connection_machine.set_mailbox()
    @m.output()
    def send_release(self):
        self._ws_send_command("release")
    @m.output()
    def notify_released(self):
        # let someone know, when both the mailbox and the nameplate are
        # released, the websocket can be closed, and we're done
        pass

    unclaimed.upon(list_nameplates, enter=unclaimed, outputs=[send_list_nameplates])
    unclaimed.upon(got_nameplates, enter=unclaimed, outputs=[notify_nameplates])
    unclaimed.upon(learned_nameplate, enter=claiming, outputs=[send_claim])
    claiming.upon(claim_acked, enter=claimed, outputs=[c_set_nameplate,
                                                       c_set_mailbox])
    claiming.upon(learned_nameplate, enter=claiming, outputs=[])
    claimed.upon(release, enter=releasing, outputs=[send_release])
    claimed.upon(learned_nameplate, enter=claimed, outputs=[])
    releasing.upon(release, enter=releasing, outputs=[])
    releasing.upon(release_acked, enter=unclaimed, outputs=[notify_released])
    releasing.upon(learned_nameplate, enter=releasing, outputs=[])



class MailboxMachine(object):
    m = MethodicalMachine()

    @m.state()
    def closed(initial=True): pass
    @m.state()
    def open(): pass
    @m.state()
    def key_established(): pass
    @m.state()
    def key_verified(): pass

    @m.input()
    def m_set_code(self, code): pass

    @m.input()
    def m_set_mailbox(self, mailbox):
        """Call m_set_mailbox() when you learn the mailbox id, either from
        the response to claim_nameplate, or because we started from a
        Wormhole Seed"""
        pass
    @m.input()
    def message_pake(self, pake): pass # reponse["message"][phase=pake]
    @m.input()
    def message_version(self, version): # response["message"][phase=version]
        pass
    @m.input()
    def message_app(self, msg): # response["message"][phase=\d+]
        pass
    @m.input()
    def close(self): pass

    @m.output()
    def send_pake(self, pake):
        self._ws_send_command("add", phase="pake", body=XXX(pake))
    @m.output()
    def send_version(self, pake): # XXX remove pake=
        plaintext = dict_to_bytes(self._my_versions)
        phase = "version"
        data_key = self._derive_phase_key(self._side, phase)
        encrypted = self._encrypt_data(data_key, plaintext)
        self._msg_send(phase, encrypted)
    @m.output()
    def c_remove_mailbox(self):
        self._connection.c_remove_mailbox()

    # decrypt, deliver up to app



    @m.output()
    def open_mailbox(self, mailbox):
        self._ws_send_command("open", mailbox=mailbox)

    @m.output()
    def close_mailbox(self, mood):
        self._ws_send_command("close", mood=mood)

    closed.upon(m_set_mailbox, enter=open, outputs=[open_mailbox])
    open.upon(message_pake, enter=key_established, outputs=[send_pake,
                                                            send_version])
    key_established.upon(message_version, enter=key_verified, outputs=[])
    key_verified.upon(close, enter=closed, outputs=[c_remove_mailbox])
