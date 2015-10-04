from __future__ import print_function
from twisted.internet import reactor, endpoints
from twisted.application import service
from twisted.web import server, static, resource
from ..util.endpoint_service import ServerEndpointService
from .. import __version__
from ..database import get_db
from .relay_server import Relay
from .transit_server import Transit

class Root(resource.Resource):
    # child_FOO is a nevow thing, not a twisted.web.resource thing
    def __init__(self):
        resource.Resource.__init__(self)
        self.putChild(b"", static.Data(b"Wormhole Relay\n", "text/plain"))

class RelayServer(service.MultiService):
    def __init__(self, relayport, transitport, advertise_version,
                 db_url=":memory:"):
        service.MultiService.__init__(self)
        self.db = get_db(db_url)
        welcome = {
            "current_version": __version__,
            # adding .motd will cause all clients to display the message,
            # then keep running normally
            #"motd": "Welcome to the public relay.\nPlease enjoy this service.",
            #
            # adding .error will cause all clients to fail, with this message
            #"error": "This server has been disabled, see URL for details.",
            }
        if advertise_version:
            welcome["current_version"] = advertise_version
        self.root = Root()
        site = server.Site(self.root)
        r = endpoints.serverFromString(reactor, relayport)
        self.relayport_service = ServerEndpointService(r, site)
        self.relayport_service.setServiceParent(self)
        self.relay = Relay(self.db, welcome) # accessible from tests
        self.root.putChild(b"wormhole-relay", self.relay)
        if transitport:
            self.transit = Transit()
            self.transit.setServiceParent(self) # for the timer
            t = endpoints.serverFromString(reactor, transitport)
            self.transport_service = ServerEndpointService(t, self.transit)
            self.transport_service.setServiceParent(self)
