from six.moves.urllib_parse import urlparse
from twisted.internet import defer, reactor
from ._machine import Machine

class ConnectionMachine:
    def __init__(self, ws_url):
        self._ws_url = ws_url
        #self._f = f = WSFactory(self._ws_url)
        #f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        #f.connection_machine = self # calls onOpen and onClose
        p = urlparse(self._ws_url)
        self._ep = self._make_endpoint(p.hostname, p.port or 80)
        self._connector = None
        self._done_d = defer.Deferred()

    def _make_endpoint(self, hostname, port):
        return None

    # "@action" marks a method as doing something, then moving to a state or
    # another action. "=State()" marks a state, where we want for an event.
    # "=Event()" marks an event, which causes us to move out of a state,
    # through zero or more actions, and eventually landing in some other
    # state.

    m = Machine()
    starting = m.State("starting", initial=True, color="orange")
    connecting = m.State("connecting", color="orange")
    negotiating = m.State("negotiating", color="orange")
    open = m.State("open", color="green")
    waiting = m.State("waiting", color="blue")
    reconnecting = m.State("reconnecting", color="blue")
    disconnecting = m.State("disconnecting", color="orange")
    cancelling = m.State("cancelling")
    stopped = m.State("stopped", color="orange")

    CM_start = m.Event("CM_start")
    d_callback = m.Event("d_callback")
    d_errback = m.Event("d_errback")
    onOpen = m.Event("onOpen")
    onClose = m.Event("onClose")
    stop = m.Event("stop")
    expire = m.Event("expire")

    @m.action(color="orange")
    def connect1(self):
        d = self._ep.connect()
        d.addCallbacks(self.c1_d_callback, self.c1_d_errback)
    @m.action(color="red")
    def notify_fail(self, ARGS):
        self._done_d.errback("ERR")
    @m.action(color="orange")
    def opened(self):
        self._p.send("bind")
        self._M.connected()
    @m.action()
    def dropConnectionWhileNegotiating(self):
        self._p.dropConnection()
    @m.action(color="orange")
    def dropOpenConnection(self):
        self._p.dropOpenConnection()
        self._M.lost()
    @m.action(color="blue")
    def lostConnection(self):
        self._M.lost()
    @m.action(color="blue")
    def start_timer(self):
        self._timer = reactor.callLater(self._timeout, self.expire)
    @m.action(color="blue")
    def reconnect(self):
        d = self._ep.connect()
        d.addCallbacks(self.c1_d_callback, self.c1_d_errback)
    @m.action(color="blue")
    def reset_timer(self):
        self._timeout = self.INITIAL_TIMEOUT
    @m.action()
    def cancel_timer(self):
        self._timer.cancel()
    @m.action()
    def d_cancel(self):
        self._d.cancel()
    @m.action(color="orange")
    def MC_stopped(self):
        self.MC.stopped()

    def c1_d_callback(self, p):
        self.d_callback()
    def c1_d_errback(self, f):
        self.d_errback()
    def p_onClose(self, why):
        self.onClose()
    def p_onOpen(self):
        self.onOpen()

    starting.upon(CM_start, goto=connect1, color="orange")
    connecting.upon(d_callback, goto=negotiating, color="orange")
    connecting.upon(d_errback, goto=notify_fail, color="red")
    connecting.upon(onClose, goto=notify_fail, color="red")
    connecting.upon(stop, goto=d_cancel)
    negotiating.upon(onOpen, goto=opened, color="orange")
    negotiating.upon(onClose, goto=notify_fail, color="red")
    negotiating.upon(stop, goto=dropConnectionWhileNegotiating)
    open.upon(onClose, goto=lostConnection, color="blue")
    open.upon(stop, goto=dropOpenConnection, color="orange")
    waiting.upon(expire, goto=reconnect, color="blue")
    waiting.upon(stop, goto=cancel_timer)
    reconnecting.upon(d_callback, goto=reset_timer, color="blue")
    reconnecting.upon(d_errback, goto=start_timer)
    reconnecting.upon(stop, goto=d_cancel)
    disconnecting.upon(onClose, goto=MC_stopped, color="orange")
    cancelling.upon(d_errback, goto=MC_stopped)

    connect1.goto(connecting, color="orange")
    notify_fail.goto(MC_stopped, color="red")
    opened.goto(open, color="orange")
    dropConnectionWhileNegotiating.goto(disconnecting)
    dropOpenConnection.goto(disconnecting, color="orange")
    lostConnection.goto(start_timer, color="blue")
    start_timer.goto(waiting, color="blue")
    reconnect.goto(reconnecting, color="blue")
    reset_timer.goto(negotiating, color="blue")
    cancel_timer.goto(MC_stopped)
    d_cancel.goto(cancelling)
    MC_stopped.goto(stopped, color="orange")


CM = ConnectionMachine("ws://host")
#CM.CM_start()

if __name__ == "__main__":
    import sys
    CM.m._dump_dot(sys.stdout)
