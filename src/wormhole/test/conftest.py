from twisted.internet import endpoints

from wormhole_mailbox_server.database import create_channel_db, create_usage_db
from wormhole_mailbox_server.server import make_server
from wormhole_mailbox_server.web import make_web_server
from wormhole import create

import pytest
import pytest_twisted


@pytest.fixture(scope="session")
def reactor():
    from twisted.internet import reactor
    ## future: reactor = MemoryReactorClockResolver()
    yield reactor


@pytest.fixture(scope="session")
def eventual_queue(reactor):
    yield EventualQueue(reactor)


@pytest.fixture(scope="session")
def mailbox(reactor):
    db = create_channel_db(":memory:")
    usage_db = create_usage_db(":memory:")
    rendezvous = make_server(db, usage_db=usage_db)
    ep = endpoints.TCP4ServerEndpoint(reactor, 0, interface="127.0.0.1")
    site = make_web_server(rendezvous, log_requests=False)
    port = pytest_twisted.blockon(ep.listen(site))

    yield f"ws://127.0.0.1:{port._realPortNumber}/v1"  # XXX private API

    pytest_twisted.blockon(port.stopListening())

@pytest.fixture(scope="session")
def transit_relay(mailbox):
    transitport = allocate_tcp_port()
    ep = endpoints.serverFromString(
        reactor, "tcp:%d:interface=127.0.0.1" % transitport)

    usage = create_usage_tracker(blur_usage=None, log_file=None, usage_db=None)
    transit_server = protocol.ServerFactory()
    transit_server.protocol = TransitConnection
    transit_server.log_requests = False
    transit_server.transit = Transit(usage, reactor.seconds)

    srv = internet.StreamServerEndpointService(ep, transit_server) #.setServiceParent(self.sp)
    srv.start()
    yield u"tcp:127.0.0.1:%d" % self.transitport # return the actual thing
    srv.stop() # clean up our trash

@pytest.fixture()
def wormhole(mailbox,reactor):
    w = create("foo",mailbox,reactor)
    yield w
