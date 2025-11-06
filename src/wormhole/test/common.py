# no unicode_literals until twisted update
from attrs import define
from click.testing import CliRunner
from twisted.application import internet, service
from twisted.internet import defer, endpoints, reactor, protocol
from twisted.python import log

from unittest import mock
from wormhole_mailbox_server.database import create_channel_db, create_usage_db
from wormhole_mailbox_server.server import make_server, Server
from wormhole_mailbox_server.web import make_web_server, PrivacyEnhancedSite
from wormhole_transit_relay.transit_server import Transit, TransitConnection
from wormhole_transit_relay.usage import create_usage_tracker

import sqlite3

from ..cli import cli
from ..transit import allocate_tcp_port


class MyInternetService(service.Service):
    # like StreamServerEndpointService, but you can retrieve the port
    def __init__(self, endpoint, factory):
        self.endpoint = endpoint
        self.factory = factory
        self._port_d = defer.Deferred()
        self._lp = None

    def startService(self):
        super().startService()
        d = self.endpoint.listen(self.factory)

        def good(lp):
            self._lp = lp
            self._port_d.callback(lp.getHost().port)

        def bad(f):
            log.err(f)
            self._port_d.errback(f)

        d.addCallbacks(good, bad)

    async def stopService(self):
        if self._lp:
            await self._lp.stopListening()

    def getPort(self):  # only call once!
        return self._port_d

@define
class Mailbox:
    channel_db: sqlite3.Connection
    usage_db: sqlite3.Connection
    rendezvous: Server
    web: PrivacyEnhancedSite
    url: str
    service: internet.StreamServerEndpointService
    port: object  # IPort ?
    site: object  # ??


async def setup_mailbox(reactor, advertise_version=None, error=None):
    """
    Set up an in-memory Mailbox server.

    If `advertise_version` is not `None`, we advertise it
    If `error` is not `None` we include `error` in the Welcome

    NOTE: Caller is responsible for starting and stopping the service

    :returns: two-tuple of (relay-url, IService instance).
    """
    db = create_channel_db(":memory:")
    usage_db = create_usage_db(":memory:")
    rendezvous = make_server(
        db,
        usage_db=usage_db,
        advertise_version=advertise_version,
        signal_error=error,
    )
    ep = endpoints.TCP4ServerEndpoint(reactor, 0, interface="127.0.0.1")
    site = make_web_server(rendezvous, log_requests=False)
    port = await ep.listen(site)
    service = internet.StreamServerEndpointService(ep, site)
    relay_url = f"ws://127.0.0.1:{port._realPortNumber}/v1"  # XXX private API
    return Mailbox(db, usage_db, rendezvous, site, relay_url, service, port, site)


def setup_transit_relay(reactor):
    transitport = allocate_tcp_port()
    endpoint = f"tcp:{transitport}:interface=127.0.0.1"
    client_endpoint = f"tcp:127.0.0.1:{transitport}"
    ep = endpoints.serverFromString(reactor, endpoint)
    usage = create_usage_tracker(blur_usage=None, log_file=None, usage_db=None)
    transit_server = protocol.ServerFactory()
    transit_server.protocol = TransitConnection
    transit_server.log_requests = False
    transit_server.transit = Transit(usage, reactor.seconds)
    service = internet.StreamServerEndpointService(ep, transit_server)
    return client_endpoint, service


# XXX missing from ServerBase replacement fixtures:

        # Unit tests that spawn a (blocking) client in a thread might still
        # have threads running at this point, if one is stuck waiting for a
        # message from a companion which has exited with an error. Our
        # relay's .stopService() drops all connections, which ought to
        # encourage those threads to terminate soon. If they don't, print a
        # warning to ease debugging.

        # XXX FIXME there's something in _noclobber test that's not
        # waiting for a close, I think -- was pretty relieably getting
        # unclean-reactor, but adding a slight pause here stops it...

"""
        tp = reactor.getThreadPool()
        if not tp.working:
            await self.sp.stopService()
            await task.deferLater(reactor, 0.1, lambda: None)
            return None
        # disconnect all callers
        d = defer.maybeDeferred(self.sp.stopService)
        d.addBoth(lambda _: self._transit_server.stopFactory())
        # wait a second, then check to see if it worked
        await task.deferLater(reactor, 1.0, lambda: None)
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
        await d
"""


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


async def poll_until(predicate):
    # return a Deferred that won't fire until the predicate is True
    while not predicate():
        d = defer.Deferred()
        reactor.callLater(0.001, d.callback, None)
        await d
