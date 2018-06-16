# no unicode_literals untill twisted update
from click.testing import CliRunner
from twisted.application import internet, service
from twisted.internet import defer, endpoints, reactor, task
from twisted.python import log

import mock
from wormhole_mailbox_server.database import create_channel_db, create_usage_db
from wormhole_mailbox_server.server import make_server
from wormhole_mailbox_server.web import make_web_server
from wormhole_transit_relay.transit_server import Transit

from ..cli import cli
from ..transit import allocate_tcp_port


class MyInternetService(service.Service, object):
    # like StreamServerEndpointService, but you can retrieve the port
    def __init__(self, endpoint, factory):
        self.endpoint = endpoint
        self.factory = factory
        self._port_d = defer.Deferred()
        self._lp = None

    def startService(self):
        super(MyInternetService, self).startService()
        d = self.endpoint.listen(self.factory)

        def good(lp):
            self._lp = lp
            self._port_d.callback(lp.getHost().port)

        def bad(f):
            log.err(f)
            self._port_d.errback(f)

        d.addCallbacks(good, bad)

    @defer.inlineCallbacks
    def stopService(self):
        if self._lp:
            yield self._lp.stopListening()

    def getPort(self):  # only call once!
        return self._port_d


class ServerBase:
    @defer.inlineCallbacks
    def setUp(self):
        yield self._setup_relay(None)

    @defer.inlineCallbacks
    def _setup_relay(self, error, advertise_version=None):
        self.sp = service.MultiService()
        self.sp.startService()
        # need to talk to twisted team about only using unicode in
        # endpoints.serverFromString
        db = create_channel_db(":memory:")
        self._usage_db = create_usage_db(":memory:")
        self._rendezvous = make_server(
            db,
            advertise_version=advertise_version,
            signal_error=error,
            usage_db=self._usage_db)
        ep = endpoints.TCP4ServerEndpoint(reactor, 0, interface="127.0.0.1")
        site = make_web_server(self._rendezvous, log_requests=False)
        # self._lp = yield ep.listen(site)
        s = MyInternetService(ep, site)
        s.setServiceParent(self.sp)
        self.rdv_ws_port = yield s.getPort()
        self._relay_server = s
        # self._rendezvous = s._rendezvous
        self.relayurl = u"ws://127.0.0.1:%d/v1" % self.rdv_ws_port
        # ws://127.0.0.1:%d/wormhole-relay/ws

        self.transitport = allocate_tcp_port()
        ep = endpoints.serverFromString(
            reactor, "tcp:%d:interface=127.0.0.1" % self.transitport)
        self._transit_server = f = Transit(
            blur_usage=None, log_file=None, usage_db=None)
        internet.StreamServerEndpointService(ep, f).setServiceParent(self.sp)
        self.transit = u"tcp:127.0.0.1:%d" % self.transitport

    @defer.inlineCallbacks
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

        tp = reactor.getThreadPool()
        if not tp.working:
            yield self.sp.stopService()
            yield task.deferLater(reactor, 0.1, lambda: None)
            defer.returnValue(None)
        # disconnect all callers
        d = defer.maybeDeferred(self.sp.stopService)
        # wait a second, then check to see if it worked
        yield task.deferLater(reactor, 1.0, lambda: None)
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
        yield d


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


@defer.inlineCallbacks
def poll_until(predicate):
    # return a Deferred that won't fire until the predicate is True
    while not predicate():
        d = defer.Deferred()
        reactor.callLater(0.001, d.callback, None)
        yield d
