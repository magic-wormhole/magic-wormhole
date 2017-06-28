from twisted.application.service import ServiceMaker

wormhole_server = ServiceMaker("wormhole-server",
                               "wormhole.server.service",
                               "Magic-Wormhole Rendezvous+Transit Server",
                               "wormhole-server")
