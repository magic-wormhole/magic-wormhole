from twisted.application import service
from ..twisted.util import allocate_ports
from ..servers.server import RelayServer
from .. import __version__

class ServerBase:
    def setUp(self):
        self.sp = service.MultiService()
        self.sp.startService()
        d = allocate_ports()
        def _got_ports(ports):
            relayport, transitport = ports
            s = RelayServer("tcp:%d:interface=127.0.0.1" % relayport,
                            "tcp:%s:interface=127.0.0.1" % transitport,
                            __version__)
            s.setServiceParent(self.sp)
            self._relay_server = s.relay
            self.relayurl = u"http://127.0.0.1:%d/wormhole-relay/" % relayport
            self.transit = "tcp:127.0.0.1:%d" % transitport
        d.addCallback(_got_ports)
        return d

    def tearDown(self):
        return self.sp.stopService()
