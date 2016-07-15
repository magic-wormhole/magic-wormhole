# no unicode_literals untill twisted update
from twisted.application import service
from twisted.internet import defer, task
from twisted.python import log
from click.testing import CliRunner
import mock
from ..cli import cli
from ..transit import allocate_tcp_port
from ..server.server import RelayServer
from .. import __version__

class ServerBase:
    def setUp(self):
        self._setup_relay(None)

    def _setup_relay(self, error):
        self.sp = service.MultiService()
        self.sp.startService()
        self.relayport = allocate_tcp_port()
        self.transitport = allocate_tcp_port()
        # need to talk to twisted team about only using unicode in
        # endpoints.serverFromString
        s = RelayServer("tcp:%d:interface=127.0.0.1" % self.relayport,
                        "tcp:%s:interface=127.0.0.1" % self.transitport,
                        advertise_version=__version__,
                        signal_error=error)
        s.setServiceParent(self.sp)
        self._rendezvous = s._rendezvous
        self._transit_server = s._transit
        self.relayurl = u"ws://127.0.0.1:%d/v1" % self.relayport
        self.rdv_ws_port = self.relayport
        # ws://127.0.0.1:%d/wormhole-relay/ws
        self.transit = u"tcp:127.0.0.1:%d" % self.transitport

    def tearDown(self):
        # Unit tests that spawn a (blocking) client in a thread might still
        # have threads running at this point, if one is stuck waiting for a
        # message from a companion which has exited with an error. Our
        # relay's .stopService() drops all connections, which ought to
        # encourage those threads to terminate soon. If they don't, print a
        # warning to ease debugging.

        # XXX FIXME there's something in _noclobber test that's not
        # waiting for a close, I think -- was pretty relieably getting
        # unclean-reactor, but adding a slight pause here stops it...
        from twisted.internet import reactor

        tp = reactor.getThreadPool()
        if not tp.working:
            d = defer.succeed(None)
            d.addCallback(lambda _: self.sp.stopService())
            d.addCallback(lambda _: task.deferLater(reactor, 0.1, lambda: None))
            return d
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

def config(*argv):
    r = CliRunner()
    with mock.patch("wormhole.cli.cli.go") as go:
        res = r.invoke(cli.wormhole, argv, catch_exceptions=False)
        if res.exit_code != 0:
            print(res.exit_code)
            print(res.output)
            print(res)
            assert 0
        cfg = go.call_args[0][1]
    return cfg

