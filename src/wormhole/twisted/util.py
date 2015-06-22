from twisted.internet import defer, protocol, endpoints, reactor

def allocate_port():
    ep = endpoints.serverFromString(reactor, "tcp:0:interface=127.0.0.1")
    d = ep.listen(protocol.Factory())
    def _listening(lp):
        port = lp.getHost().port
        d2 = lp.stopListening()
        d2.addCallback(lambda _: port)
        return d2
    d.addCallback(_listening)
    return d

def allocate_ports():
    d = defer.DeferredList([allocate_port(), allocate_port()])
    def _done(results):
        port1 = results[0][1]
        port2 = results[1][1]
        return (port1, port2)
    d.addCallback(_done)
    return d
