
class ConnectionMachine:
    def __init__(self):
        self._f = f = WSFactory(self._ws_url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        f.connection_machine = self # calls onOpen and onClose
        p = urlparse(self._ws_url)
        self._ep = self._make_endpoint(p.hostname, p.port or 80)
        self._connector = None
        self._done_d = defer.Deferred()

    # "@action" marks a method as doing something, then moving to a state or
    # another action. "=State()" marks a state, where we want for an event.
    # "=Event()" marks an event, which causes us to move out of a state,
    # through zero or more actions, and eventually landing in some other
    # state.

    starting = State(initial=True)
    connecting = State()
    negotiating = State()
    open = State()
    waiting = State()
    reconnecting = State()
    disconnecting = State()
    cancelling = State()
    stopped = State()

    CM_start = Event()
    d_callback = Event()
    d_errback = Event()
    onOpen = Event()
    onClose = Event()
    stop = Event()
    expire = Event()

    @action(goto=connecting)
    def connect1(self):
        d = self._ep.connect()
        d.addCallbacks(self.c1_d_callback, self.c1_d_errback)
    @action(goto=failed)
    def notify_fail(self, ARGS?):
        stuff()
    @action(goto=open)
    def opened(self):
        tx_bind()
        M_connected()
    @action(goto=disconnecting)
    def dropConnectionWhileNegotiating(self):
        p.dropConnection()
    @action(goto=disconnecting)
    def dropOpenConnection(self):
        p.dropOpenConnection()
        M_lost()
    @action(goto=start_timer)
    def lostConnection(self):
        M_lost()
    @action(goto=waiting):
    def start_timer(self):
        self._timer = reactor.callLater(self._timeout, self.expire)
    @action(goto=reconnecting)
    def reconnect(self):
        d = self._ep.connect()
        d.addCallbacks(self.c1_d_callback, self.c1_d_errback)
    @action(goto=negotiating)
    def reset_timer(self):
        self._timeout = self.INITIAL_TIMEOUT
    @action(goto=MC_stopped)
    def cancel_timer(self):
        self._timer.cancel()
    @action(goto=cancelling)
    def d_cancel(self):
        self._d.cancel()
    @action(goto=stopped)
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

    starting.upon(CM_start, goto=connect1)
    connecting.upon(d_callback, goto=negotiating)
    connecting.upon(d_errback, goto=notify_fail)
    connecting.upon(onClose, goto=notify_fail)
    negotiating.upon(onOpen, goto=opened)
    negotiating.upon(onClose, goto=notify_fail)
    negotiating.upon(stop, goto=dropConnectionWhileNegotiating)
    open.upon(onClose, goto=lostConnection)
    open.upon(stop, goto=dropOpenConnection)
    waiting.upon(expire, goto=reconnect)
    waiting.upon(stop, goto=cancel_timer)
    reconnecting.upon(d_callback, goto=reset_timer)
    reconnecting.upon(stop, goto=d_cancel)
    disconnecting.upon(onClose, goto=MC_stopped)
    cancelling.upon(d_errback, goto=MC_stopped)

CM = ConnectionMachine()
CM.CM_start()
