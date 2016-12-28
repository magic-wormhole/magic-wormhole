
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
        return "S_"+self._name.replace(" ", "_")
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
        return "E_"+self._name.replace(" ", "_")
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
        self._finalized = False

    def _maybe_finalize(self):
        if self._finalized:
            return
        # do final consistency checks: are all events handled?

    def _maybe_start(self):
        self._maybe_finalize()
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
        self._maybe_finalize()
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

    # all descriptions are from the state machine's point of view
    # States are gerunds: Foo-ing
    # Events are past-tense verbs: Foo-ed, as in "I have been Foo-ed"
    # * machine.do(event) ? vs machine.fooed()
    # Actions are immediate-tense verbs: foo, connect

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
