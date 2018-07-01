
class ManagerFollower(_ManagerBase):
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)

    @m.state(initial=True)
    def IDLE(self): pass # pragma: no cover

    @m.state()
    def WANTING(self): pass # pragma: no cover
    @m.state()
    def CONNECTING(self): pass # pragma: no cover
    @m.state()
    def CONNECTED(self): pass # pragma: no cover
    @m.state(terminal=True)
    def STOPPED(self): pass # pragma: no cover

    @m.input()
    def start(self): pass # pragma: no cover
    @m.input()
    def rx_PLEASE(self): pass # pragma: no cover
    @m.input()
    def rx_DILATE(self): pass # pragma: no cover
    @m.input()
    def rx_HINTS(self, hint_message): pass # pragma: no cover

    @m.input()
    def connection_made(self): pass # pragma: no cover
    @m.input()
    def connection_lost(self): pass # pragma: no cover
    # follower doesn't react to connection_lost, but waits for a new LETS_DILATE

    @m.input()
    def stop(self): pass # pragma: no cover

    # these Outputs behave differently for the Leader vs the Follower
    @m.output()
    def send_please(self):
        self.send_dilation_phase(type="please")

    @m.output()
    def start_connecting(self):
        self._start_connecting(FOLLOWER)

    # these Outputs delegate to the same code in both the Leader and the
    # Follower, but they must be replicated here because the Automat instance
    # is on the subclass, not the shared superclass

    @m.output()
    def use_hints(self, hint_message):
        hint_objs = filter(lambda h: h, # ignore None, unrecognizable
                           [parse_hint(hs) for hs in hint_message["hints"]])
        self._connector.got_hints(hint_objs)
    @m.output()
    def stop_connecting(self):
        self._connector.stop()
    @m.output()
    def use_connection(self, c):
        self._use_connection(c)
    @m.output()
    def stop_using_connection(self):
        self._stop_using_connection()
    @m.output()
    def signal_error(self):
        pass # TODO
    @m.output()
    def signal_error_hints(self, hint_message):
        pass # TODO

    IDLE.upon(rx_HINTS, enter=STOPPED, outputs=[signal_error_hints]) # too early
    IDLE.upon(rx_DILATE, enter=STOPPED, outputs=[signal_error]) # too early
    # leader shouldn't send us DILATE before receiving our PLEASE
    IDLE.upon(stop, enter=STOPPED, outputs=[])
    IDLE.upon(start, enter=WANTING, outputs=[send_please])
    WANTING.upon(rx_DILATE, enter=CONNECTING, outputs=[start_connecting])
    WANTING.upon(stop, enter=STOPPED, outputs=[])

    CONNECTING.upon(rx_HINTS, enter=CONNECTING, outputs=[use_hints])
    CONNECTING.upon(connection_made, enter=CONNECTED, outputs=[use_connection])
    # shouldn't happen: connection_lost
    #CONNECTING.upon(connection_lost, enter=CONNECTING, outputs=[?])
    CONNECTING.upon(rx_DILATE, enter=CONNECTING, outputs=[stop_connecting,
                                                          start_connecting])
    # receiving rx_DILATE while we're still working on the last one means the
    # leader thought we'd connected, then thought we'd been disconnected, all
    # before we heard about that connection
    CONNECTING.upon(stop, enter=STOPPED, outputs=[stop_connecting])

    CONNECTED.upon(connection_lost, enter=WANTING, outputs=[stop_using_connection])
    CONNECTED.upon(rx_DILATE, enter=CONNECTING, outputs=[stop_using_connection,
                                                         start_connecting])
    CONNECTED.upon(rx_HINTS, enter=CONNECTED, outputs=[]) # too late, ignore
    CONNECTED.upon(stop, enter=STOPPED, outputs=[stop_using_connection])
    # shouldn't happen: connection_made

    # we should never receive PLEASE, we're the follower
    IDLE.upon(rx_PLEASE, enter=STOPPED, outputs=[signal_error])
    WANTING.upon(rx_PLEASE, enter=STOPPED, outputs=[signal_error])
    CONNECTING.upon(rx_PLEASE, enter=STOPPED, outputs=[signal_error])
    CONNECTED.upon(rx_PLEASE, enter=STOPPED, outputs=[signal_error])

    def allocate_subchannel_id(self):
        # the follower uses even numbers starting with 2
        scid_num = self._next_outbound_seqnum + 2
        self._next_outbound_seqnum += 2
        return to_be4(scid_num)
