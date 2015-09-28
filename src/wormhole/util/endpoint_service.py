from twisted.python import log
from twisted.internet import defer
from twisted.application import service

# this should probably live in Twisted

class ServerEndpointService(service.Service):
    def __init__(self, endpoint, factory):
        self.endpoint = endpoint
        self.factory = factory
        self._started = defer.Deferred()
        self._listeningport = None

    def startService(self):
        d = self.endpoint.listen(self.factory)
        def _set_port(listeningport):
            self._listeningport = listeningport
            self._started.callback(listeningport)
        d.addCallback(_set_port)
        d.addErrback(log.err)

    def stopService(self):
        def _stop(port):
            return port.stopListening()
        self._started.addCallback(_stop)
        return self._started
