
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
