
from six.moves.urllib_parse import urlparse
from attr import attrs, attrib
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


# pip install (path to automat checkout)[visualize]
# automat-visualize wormhole._connection

# We have one WSRelayClient for each wsurl we know about, and it lasts
# as long as its parent Wormhole does.

@attrs
class WSRelayClient(object):
    _wormhole = attrib()
    _ws_url = attrib()
    _reactor = attrib()

    m = MethodicalMachine()
    ALLOW_CLOSE = True

    def __init__(self):
        self._f = f = WSFactory(self._ws_url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        f.connection_machine = self # calls onOpen and onClose
        p = urlparse(self._ws_url)
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
    def d_cancel(self, f): pass # XXX remove f
    @m.input()
    def onOpen(self, ws): pass ; print("in onOpen")
    @m.input()
    def onClose(self, f): pass # XXX maybe remove f
    @m.input()
    def expire(self): pass
    if ALLOW_CLOSE:
        @m.input()
        def close(self, f): pass

    @m.output()
    def ep_connect(self):
        "ep.connect()"
        print("ep_connect()")
        self._d = self._ep.connect(self._f)
        self._d.addCallbacks(self.d_callback, self.d_errback)
    @m.output()
    def add_connection(self, ws):
        print("add_connection", ws)
        self._connection = WSConnection(ws, self._wormhole.appid,
                                        self._wormhole.side, self)
        self._wormhole.add_connection(self._connection)
    @m.output()
    def remove_connection(self, f): # XXX remove f
        self._wormhole.remove_connection(self._connection)
        self._connection = None
    @m.output()
    def start_timer(self, f): # XXX remove f
        print("start_timer")
        self._t = self._reactor.callLater(3.0, self.expire)
    @m.output()
    def cancel_timer(self, f): # XXX remove f
        print("cancel_timer")
        self._t.cancel()
        self._t = None
    @m.output()
    def dropConnection(self, f): # XXX remove f
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
        first_time_connecting.upon(close, enter=disconnecting2,
                                   outputs=[d_cancel])
        disconnecting2.upon(d_errback, enter=closed, outputs=[])

    negotiating.upon(onOpen, enter=open, outputs=[add_connection])
    if ALLOW_CLOSE:
        negotiating.upon(close, enter=disconnecting, outputs=[dropConnection])
    negotiating.upon(onClose, enter=failed, outputs=[notify_fail])

    open.upon(onClose, enter=waiting, outputs=[remove_connection, start_timer])
    if ALLOW_CLOSE:
        open.upon(close, enter=disconnecting,
                  outputs=[dropConnection, remove_connection])
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

# a new WSConnection is created each time the WSRelayClient gets through
# negotiation
@attrs
class WSConnection(object):
    _ws = attrib()
    _appid = attrib()
    _side = attrib()
    _wsrc = attrib()
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
    def wsc_set_nameplate(self): pass
    @m.input()
    def wsc_set_mailbox(self, mailbox): pass
    @m.input()
    def wsc_release_nameplate(self): pass
    @m.input()
    def wsc_release_mailbox(self): pass
    @m.input()
    def ack_close(self): pass

    @m.output()
    def send_bind(self):
        self._ws_send_command("bind", appid=self._appid, side=self._side)
    @m.output()
    def notify_bound(self):
        self._nameplate_machine.bound()
        self._connection.make_listing_machine()
    @m.output()
    def m_set_mailbox(self, mailbox):
        self._mailbox_machine.m_set_mailbox(mailbox)
    @m.output()
    def request_close(self):
        self._wsrc.close()
    @m.output()
    def notify_close(self):
        pass

    unbound.upon(bind, enter=binding, outputs=[send_bind])
    binding.upon(ack_bind, enter=neither, outputs=[notify_bound])
    neither.upon(wsc_set_nameplate, enter=has_nameplate, outputs=[])
    neither.upon(wsc_set_mailbox, enter=has_mailbox, outputs=[m_set_mailbox])
    has_nameplate.upon(wsc_set_mailbox, enter=has_both, outputs=[m_set_mailbox])
    has_nameplate.upon(wsc_release_nameplate, enter=closing, outputs=[request_close])
    has_mailbox.upon(wsc_set_nameplate, enter=has_both, outputs=[])
    has_mailbox.upon(wsc_release_mailbox, enter=closing, outputs=[request_close])
    has_both.upon(wsc_release_nameplate, enter=has_mailbox, outputs=[])
    has_both.upon(wsc_release_mailbox, enter=has_nameplate, outputs=[])
    closing.upon(ack_close, enter=closed, outputs=[])

class NameplateMachine(object):
    m = MethodicalMachine()

    @m.state(initial=True)
    def unclaimed(self): pass # but bound
    @m.state()
    def claiming(self): pass
    @m.state()
    def claimed(self): pass
    @m.state()
    def releasing(self): pass
    @m.state(terminal=True)
    def done(self): pass

    @m.input()
    def learned_nameplate(self, nameplate):
        """Call learned_nameplate() when you learn the nameplate: either
        through allocation or code entry"""
        pass
    @m.input()
    def rx_claimed(self, mailbox): pass # response("claimed")
    @m.input()
    def nm_release_nameplate(self): pass
    @m.input()
    def release_acked(self): pass # response("released")

    @m.output()
    def send_claim(self, nameplate):
        self._ws_send_command("claim", nameplate=nameplate)
    @m.output()
    def wsc_set_nameplate(self, mailbox):
        self._connection_machine.wsc_set_nameplate()
    @m.output()
    def wsc_set_mailbox(self, mailbox):
        self._connection_machine.wsc_set_mailbox()
    @m.output()
    def mm_set_mailbox(self, mailbox):
        self._mm.mm_set_mailbox()
    @m.output()
    def send_release(self):
        self._ws_send_command("release")
    @m.output()
    def wsc_release_nameplate(self):
        # let someone know, when both the mailbox and the nameplate are
        # released, the websocket can be closed, and we're done
        self._wsc.wsc_release_nameplate()

    unclaimed.upon(learned_nameplate, enter=claiming, outputs=[send_claim])
    claiming.upon(rx_claimed, enter=claimed, outputs=[wsc_set_nameplate,
                                                      mm_set_mailbox,
                                                      wsc_set_mailbox])
    #claiming.upon(learned_nameplate, enter=claiming, outputs=[])
    claimed.upon(nm_release_nameplate, enter=releasing, outputs=[send_release])
    #claimed.upon(learned_nameplate, enter=claimed, outputs=[])
    #releasing.upon(release, enter=releasing, outputs=[])
    releasing.upon(release_acked, enter=done, outputs=[wsc_release_nameplate])
    #releasing.upon(learned_nameplate, enter=releasing, outputs=[])

class NameplateListingMachine(object):
    m = MethodicalMachine()
    def __init__(self):
        self._list_nameplate_waiters = []

    # Ideally, each API request would spawn a new "list_nameplates" message
    # to the server, so the response would be maximally fresh, but that would
    # require correlating server request+response messages, and the protocol
    # is intended to be less stateful than that. So we offer a weaker
    # freshness property: if no server requests are in flight, then a new API
    # request will provoke a new server request, and the result will be
    # fresh. But if a server request is already in flight when a second API
    # request arrives, both requests will be satisfied by the same response.

    @m.state(initial=True)
    def idle(self): pass
    @m.state()
    def requesting(self): pass

    @m.input()
    def list_nameplates(self): pass # returns Deferred
    @m.input()
    def response(self, message): pass

    @m.output()
    def add_deferred(self):
        d = defer.Deferred()
        self._list_nameplate_waiters.append(d)
        return d
    @m.output()
    def send_request(self):
        self._connection.send_command("list")
    @m.output()
    def distribute_response(self, message):
        nameplates = parse(message)
        waiters = self._list_nameplate_waiters
        self._list_nameplate_waiters = []
        for d in waiters:
            d.callback(nameplates)

    idle.upon(list_nameplates, enter=requesting,
              outputs=[add_deferred, send_request],
              collector=lambda outs: outs[0])
    idle.upon(response, enter=idle, outputs=[])
    requesting.upon(list_nameplates, enter=requesting,
                    outputs=[add_deferred],
                    collector=lambda outs: outs[0])
    requesting.upon(response, enter=idle, outputs=[distribute_response])

    # nlm._connection = c = Connection(ws)
    # nlm.list_nameplates().addCallback(display_completions)
    # c.register_dispatch("nameplates", nlm.response)

class MailboxMachine(object):
    m = MethodicalMachine()

    @m.state()
    def unknown(initial=True): pass
    @m.state()
    def mailbox_unused(): pass
    @m.state()
    def mailbox_used(): pass

    @m.input()
    def mm_set_mailbox(self, mailbox): pass
    @m.input()
    def add_connection(self, connection): pass
    @m.input()
    def rx_message(self): pass

    @m.input()
    def close(self): pass

    @m.output()
    def open_mailbox(self):
        self._mm.mm_set_mailbox(self._mailbox)
    @m.output()
    def nm_release_nameplate(self):
        self._nm.nm_release_nameplate()
    @m.output()
    def wsc_release_mailbox(self):
        self._wsc.wsc_release_mailbox()
    @m.output()
    def open_mailbox(self, mailbox):
        self._ws_send_command("open", mailbox=mailbox)

    @m.output()
    def close_mailbox(self, mood):
        self._ws_send_command("close", mood=mood)

    unknown.upon(mm_set_mailbox, enter=mailbox_unused, outputs=[open_mailbox])
    mailbox_unused.upon(rx_message, enter=mailbox_used,
                        outputs=[nm_release_nameplate])
    #open.upon(message_pake, enter=key_established, outputs=[send_pake,
    #                                                        send_version])
    #key_established.upon(message_version, enter=key_verified, outputs=[])
    #key_verified.upon(close, enter=closed, outputs=[wsc_release_mailbox])

class Wormhole:
    m = MethodicalMachine()

    def __init__(self, ws_url, reactor):
        self._relay_client = WSRelayClient(self, ws_url, reactor)
        # This records all the messages we want the relay to have. Each time
        # we establish a connection, we'll send them all (and the relay
        # server will filter out duplicates). If we add any while a
        # connection is established, we'll send the new ones.
        self._outbound_messages = []

    def start(self):
        self._relay_client.start()

    @m.state()
    def closed(initial=True): pass
    @m.state()
    def know_code_not_mailbox(): pass
    @m.state()
    def know_code_and_mailbox(): pass # no longer need nameplate
    @m.state()
    def waiting_first_msg(): pass # key is established, want any message
    @m.state()
    def processing_version(): pass
    @m.state()
    def processing_phase(): pass
    @m.state()
    def open(): pass # key is verified, can post app messages
    @m.state(terminal=True)
    def failed(): pass

    @m.input()
    def deliver_message(self, message): pass

    def w_set_seed(self, code, mailbox):
        """Call w_set_seed when we sprout a Wormhole Seed, which
        contains both the code and the mailbox"""
        self.w_set_code(code)
        self.w_set_mailbox(mailbox)

    @m.input()
    def w_set_code(self, code):
        """Call w_set_code when you learn the code, probably because the user
        typed it in."""
    @m.input()
    def w_set_mailbox(self, mailbox):
        """Call w_set_mailbox() when you learn the mailbox id, from the
        response to claim_nameplate"""
        pass


    @m.input()
    def rx_pake(self, pake): pass # reponse["message"][phase=pake]

    @m.input()
    def rx_version(self, version): # response["message"][phase=version]
        pass
    @m.input()
    def verify_good(self, verifier): pass
    @m.input()
    def verify_bad(self, f): pass

    @m.input()
    def rx_phase(self, message): pass
    @m.input()
    def phase_good(self, message): pass
    @m.input()
    def phase_bad(self, f): pass

    @m.output()
    def compute_and_post_pake(self, code):
        self._code = code
        self._pake = compute(code)
        self._post(pake=self._pake)
        self._ws_send_command("add", phase="pake", body=XXX(pake))
    @m.output()
    def set_mailbox(self, mailbox):
        self._mailbox = mailbox
    @m.output()
    def set_seed(self, code, mailbox):
        self._code = code
        self._mailbox = mailbox

    @m.output()
    def process_version(self, version): # response["message"][phase=version]
        their_verifier = com
        if OK:
            self.verify_good(verifier)
        else:
            self.verify_bad(f)
        pass

    @m.output()
    def notify_verified(self, verifier):
        for d in self._verify_waiters:
            d.callback(verifier)
    @m.output()
    def notify_failed(self, f):
        for d in self._verify_waiters:
            d.errback(f)

    @m.output()
    def process_phase(self, message): # response["message"][phase=version]
        their_verifier = com
        if OK:
            self.verify_good(verifier)
        else:
            self.verify_bad(f)
        pass

    @m.output()
    def post_inbound(self, message):
        pass

    @m.output()
    def deliver_message(self, message):
        self._qc.deliver_message(message)

    @m.output()
    def compute_key_and_post_version(self, pake):
        self._key = x
        self._verifier = x
        plaintext = dict_to_bytes(self._my_versions)
        phase = "version"
        data_key = self._derive_phase_key(self._side, phase)
        encrypted = self._encrypt_data(data_key, plaintext)
        self._msg_send(phase, encrypted)

    closed.upon(w_set_code, enter=know_code_not_mailbox,
                outputs=[compute_and_post_pake])
    know_code_not_mailbox.upon(w_set_mailbox, enter=know_code_and_mailbox,
                               outputs=[set_mailbox])
    know_code_and_mailbox.upon(rx_pake, enter=waiting_first_msg,
                               outputs=[compute_key_and_post_version])
    waiting_first_msg.upon(rx_version, enter=processing_version,
                           outputs=[process_version])
    processing_version.upon(verify_good, enter=open, outputs=[notify_verified])
    processing_version.upon(verify_bad, enter=failed, outputs=[notify_failed])
    open.upon(rx_phase, enter=processing_phase, outputs=[process_phase])
    processing_phase.upon(phase_good, enter=open, outputs=[post_inbound])
    processing_phase.upon(phase_bad, enter=failed, outputs=[notify_failed])

class QueueConnect:
    m = MethodicalMachine()
    def __init__(self):
        self._outbound_messages = []
        self._connection = None
    @m.state()
    def disconnected(): pass
    @m.state()
    def connected(): pass

    @m.input()
    def deliver_message(self, message): pass
    @m.input()
    def connect(self, connection): pass
    @m.input()
    def disconnect(self): pass

    @m.output()
    def remember_connection(self, connection):
        self._connection = connection
    @m.output()
    def forget_connection(self):
        self._connection = None
    @m.output()
    def queue_message(self, message):
        self._outbound_messages.append(message)
    @m.output()
    def send_message(self, message):
        self._connection.send(message)
    @m.output()
    def send_queued_messages(self, connection):
        for m in self._outbound_messages:
            connection.send(m)

    disconnected.upon(deliver_message, enter=disconnected, outputs=[queue_message])
    disconnected.upon(connect, enter=connected, outputs=[remember_connection,
                                                         send_queued_messages])
    connected.upon(deliver_message, enter=connected,
                   outputs=[queue_message, send_message])
    connected.upon(disconnect, enter=disconnected, outputs=[forget_connection])
