from twisted.application import service
from twisted.internet import reactor, defer
from twisted.python import log
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
            self._transit_server = s.transit
            self.relayurl = u"http://127.0.0.1:%d/wormhole-relay/" % relayport
            self.transit = u"tcp:127.0.0.1:%d" % transitport
        d.addCallback(_got_ports)
        return d

    def tearDown(self):
        # Unit tests that spawn a (blocking) client in a thread might still
        # have threads running at this point, if one is stuck waiting for a
        # message from a companion which has exited with an error. Our
        # relay's .stopService() drops all connections, which ought to
        # encourage those threads to terminate soon. If they don't, print a
        # warning to ease debugging.
        tp = reactor.getThreadPool()
        if not tp.working:
            return self.sp.stopService()
        # disconnect all callers
        d = defer.maybeDeferred(self.sp.stopService)
        wait_d = defer.Deferred()
        # wait a second, then check to see if it worked
        reactor.callLater(1.0, wait_d.callback, None)
        def _later(res):
            if len(tp.working):
                log.msg("wormhole.test.common.ServerBase.tearDown:"
                        " I was unable to convince all threads to exit.")
                tp.dumpStats()
                print("tearDown warning: threads are still active")
                print("This test will probably hang until one of the"
                      " clients gives up of their own accord.")
            else:
                log.msg("wormhole.test.common.ServerBase.tearDown:"
                        " I convinced all threads to exit.")
            return d
        wait_d.addCallback(_later)
        return wait_d
