# NO unicode_literals or static.Data() will break, because it demands
# a str on Python 2
from __future__ import print_function
import os, time, json
try:
    # 'resource' is unix-only
    from resource import getrlimit, setrlimit, RLIMIT_NOFILE
except ImportError: # pragma: nocover
    getrlimit, setrlimit, RLIMIT_NOFILE = None, None, None # pragma: nocover
from twisted.python import log
from twisted.internet import reactor, endpoints
from twisted.application import service, internet
from twisted.web import server, static
from twisted.web.resource import Resource
from autobahn.twisted.resource import WebSocketResource
from .database import get_db
from .rendezvous import Rendezvous
from .rendezvous_websocket import WebSocketRendezvousFactory

SECONDS = 1.0
MINUTE = 60*SECONDS

CHANNEL_EXPIRATION_TIME = 11*MINUTE
EXPIRATION_CHECK_PERIOD = 10*MINUTE

class Root(Resource):
    # child_FOO is a nevow thing, not a twisted.web.resource thing
    def __init__(self):
        Resource.__init__(self)
        self.putChild(b"", static.Data(b"Wormhole Relay\n", "text/plain"))

class PrivacyEnhancedSite(server.Site):
    logRequests = True
    def log(self, request):
        if self.logRequests:
            return server.Site.log(self, request)

class RelayServer(service.MultiService):

    def __init__(self, rendezvous_web_port, transit_port,
                 advertise_version, db_url=":memory:", blur_usage=None,
                 signal_error=None, stats_file=None, allow_list=True,
                 websocket_protocol_options=()):
        service.MultiService.__init__(self)
        self._blur_usage = blur_usage
        self._allow_list = allow_list
        self._db_url = db_url

        db = get_db(db_url)
        welcome = {
            # adding .motd will cause all clients to display the message,
            # then keep running normally
            #"motd": "Welcome to the public relay.\nPlease enjoy this service.",

            # adding .error will cause all clients to fail, with this message
            #"error": "This server has been disabled, see URL for details.",
            }

        if advertise_version:
            # The primary (python CLI) implementation will emit a message if
            # its version does not match this key. If/when we have
            # distributions which include older version, but we still expect
            # them to be compatible, stop sending this key.
            welcome["current_cli_version"] = advertise_version
        if signal_error:
            welcome["error"] = signal_error

        self._rendezvous = Rendezvous(db, welcome, blur_usage, self._allow_list)
        self._rendezvous.setServiceParent(self) # for the pruning timer

        root = Root()
        wsrf = WebSocketRendezvousFactory(None, self._rendezvous)
        _set_options(websocket_protocol_options, wsrf)
        root.putChild(b"v1", WebSocketResource(wsrf))

        site = PrivacyEnhancedSite(root)
        if blur_usage:
            site.logRequests = False

        r = endpoints.serverFromString(reactor, rendezvous_web_port)
        rendezvous_web_service = internet.StreamServerEndpointService(r, site)
        rendezvous_web_service.setServiceParent(self)

        self._stats_file = stats_file
        if self._stats_file and os.path.exists(self._stats_file):
            os.unlink(self._stats_file)
            # this will be regenerated immediately, but if something goes
            # wrong in dump_stats(), it's better to have a missing file than
            # a stale one
        t = internet.TimerService(EXPIRATION_CHECK_PERIOD, self.timer)
        t.setServiceParent(self)

        # make some things accessible for tests
        self._db = db
        self._root = root
        self._rendezvous_web_service = rendezvous_web_service
        self._rendezvous_websocket = wsrf

    def increase_rlimits(self):
        if getrlimit is None:
            log.msg("unable to import 'resource', leaving rlimit alone")
            return
        soft, hard = getrlimit(RLIMIT_NOFILE)
        if soft >= 10000:
            log.msg("RLIMIT_NOFILE.soft was %d, leaving it alone" % soft)
            return
        # OS-X defaults to soft=7168, and reports a huge number for 'hard',
        # but won't accept anything more than soft=10240, so we can't just
        # set soft=hard. Linux returns (1024, 1048576) and is fine with
        # soft=hard. Cygwin is reported to return (256,-1) and accepts up to
        # soft=3200. So we try multiple values until something works.
        for newlimit in [hard, 10000, 3200, 1024]:
            log.msg("changing RLIMIT_NOFILE from (%s,%s) to (%s,%s)" %
                    (soft, hard, newlimit, hard))
            try:
                setrlimit(RLIMIT_NOFILE, (newlimit, hard))
                log.msg("setrlimit successful")
                return
            except ValueError as e:
                log.msg("error during setrlimit: %s" % e)
                continue
            except:
                log.msg("other error during setrlimit, leaving it alone")
                log.err()
                return
        log.msg("unable to change rlimit, leaving it alone")

    def startService(self):
        service.MultiService.startService(self)
        self.increase_rlimits()
        log.msg("websocket listening on /wormhole-relay/ws")
        log.msg("Wormhole relay server (Rendezvous) running")
        if self._blur_usage:
            log.msg("blurring access times to %d seconds" % self._blur_usage)
            log.msg("not logging HTTP requests")
        else:
            log.msg("not blurring access times")
        if not self._allow_list:
            log.msg("listing of allocated nameplates disallowed")

    def timer(self):
        now = time.time()
        old = now - CHANNEL_EXPIRATION_TIME
        self._rendezvous.prune_all_apps(now, old)
        self.dump_stats(now, validity=EXPIRATION_CHECK_PERIOD+60)

    def dump_stats(self, now, validity):
        if not self._stats_file:
            return
        tmpfn = self._stats_file + ".tmp"

        data = {}
        data["created"] = now
        data["valid_until"] = now + validity

        start = time.time()
        data["rendezvous"] = self._rendezvous.get_stats()
        log.msg("get_stats took:", time.time() - start)

        with open(tmpfn, "wb") as f:
            # json.dump(f) has str-vs-unicode issues on py2-vs-py3
            f.write(json.dumps(data, indent=1).encode("utf-8"))
            f.write(b"\n")
        os.rename(tmpfn, self._stats_file)


def _set_options(options, factory):
    factory.setProtocolOptions(**dict(options))
