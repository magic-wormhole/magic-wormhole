
class StateMachineError(Exception):
    pass

class _Transition:
    def __init__(self, goto, color=None):
        self._goto = goto
        self._extra_dot_attrs = {}
        if color:
            self._extra_dot_attrs["color"] = color
            self._extra_dot_attrs["fontcolor"] = color
    def _dot_attrs(self):
        return self._extra_dot_attrs

class _State:
    def __init__(self, m, name, extra_dot_attrs):
        assert isinstance(m, Machine)
        self.m = m
        self._name = name
        self._extra_dot_attrs = extra_dot_attrs
        self.eventmap = {}
    def upon(self, event, goto, color=None):
        if event in self.eventmap:
            raise StateMachineError("event already registered")
        t = _Transition(goto, color=color)
        self.eventmap[event] = t
    def _dot_name(self):
        return "S_"+self._name
    def _dot_attrs(self):
        attrs = {"label": self._name}
        attrs.update(self._extra_dot_attrs)
        return attrs

class _Event:
    def __init__(self, m, name):
        assert isinstance(m, Machine)
        self.m = m
        self._name = name
    def __call__(self): # *args, **kwargs
        self.m._handle_event(self)
        # return value?
    def _dot_name(self):
        return "E_"+self._name
    def _dot_attrs(self):
        return {"label": self._name}

class _Action:
    def __init__(self, m, f, extra_dot_attrs):
        self.m = m
        self.f = f
        self._extra_dot_attrs = extra_dot_attrs
        self.next_goto = None
        self._name = f.__name__
    def goto(self, next_goto, color=None):
        if self.next_goto:
            raise StateMachineError("Action.goto() called twice")
        self.next_goto = _Transition(next_goto, color=color)
    def __call__(self): # *args, **kwargs ?
        raise StateMachineError("don't call Actions directly")
    def _dot_name(self):
        return "A_"+self._name
    def _dot_attrs(self):
        attrs = {"shape": "box", "label": self._name}
        attrs.update(self._extra_dot_attrs)
        return attrs

def format_attrs(**kwargs):
    # return "", or "[attr=value attr=value]"
    if not kwargs or all([not(v) for v in kwargs.values()]):
        return ""
    def escape(s):
        return s.replace('\n', r'\n').replace('"', r'\"')
    pieces = ['%s="%s"' % (k, escape(kwargs[k]))
              for k in sorted(kwargs)
              if kwargs[k]]
    body = " ".join(pieces)
    return "[%s]" % body

class Machine:
    def __init__(self):
        self._initial_state = None
        self._states = set()
        self._events = set()
        self._actions = set()
        self._current_state = None

    def _maybe_start(self):
        if self._current_state:
            return
        if not self._initial_state:
            raise StateMachineError("no initial state")
        self._current_state = self._initial_state

    def _handle_event(self, event): # other args?
        self._maybe_start()
        assert event in self._events
        goto = self._current_state.eventmap.get(event)
        if not goto:
            raise StateMachineError("no transition for event %s from state %s"
                                    % (event, self._current_state))
        # execute: ordering concerns here
        while not isinstance(goto, _State):
            assert isinstance(goto, _Action)
            next_goto = goto.next_goto
            goto.f() # args?
            goto = next_goto
        assert isinstance(goto, _State)
        self._current_state = goto

    def _describe(self):
        print "current state:", self._current_state

    def _dump_dot(self, f):
        f.write("digraph {\n")
        for s in sorted(self._states):
            f.write(" %s %s\n" % (s._dot_name(), format_attrs(**s._dot_attrs())))
        f.write("\n")
        for a in sorted(self._actions):
            f.write(" %s %s\n" % (a._dot_name(), format_attrs(**a._dot_attrs())))
        f.write("\n")
        for s in sorted(self._states):
            for e in sorted(s.eventmap):
                t = s.eventmap[e]
                goto = t._goto
                attrs = {"label": e._name}
                attrs.update(t._dot_attrs())
                f.write(" %s -> %s %s\n" % (s._dot_name(), goto._dot_name(),
                                            format_attrs(**attrs)))
        f.write("\n")
        for a in sorted(self._actions):
            t = a.next_goto
            f.write(" %s -> %s %s\n" % (a._dot_name(), t._goto._dot_name(),
                                        format_attrs(**t._dot_attrs())))
        f.write("}\n")


    def State(self, name, initial=False, **dot_attrs):
        s = _State(self, name, dot_attrs)
        if initial:
            if self._initial_state:
                raise StateMachineError("duplicate initial state")
            self._initial_state = s
        self._states.add(s)
        return s

    def Event(self, name):
        e = _Event(self, name)
        self._events.add(e)
        return e

    def action(self, **dotattrs):
        def wrap(f):
            a = _Action(self, f, dotattrs)
            self._actions.add(a)
            return a
        return wrap

from six.moves.urllib_parse import urlparse
from twisted.internet import defer, reactor

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
