from twisted.internet import endpoints
from twisted.internet.protocol import ServerFactory
from twisted.application.internet import StreamServerEndpointService

from wormhole_mailbox_server.database import create_channel_db, create_usage_db
from wormhole_mailbox_server.server import make_server
from wormhole_mailbox_server.web import make_web_server
from wormhole_transit_relay.transit_server import Transit, TransitConnection
from wormhole_transit_relay.usage import create_usage_tracker


import pytest
import pytest_twisted

from ..transit import allocate_tcp_port


@pytest.fixture(scope="session")
def reactor():
    from twisted.internet import reactor
    yield reactor


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
def transit_relay(reactor):
    transitport = allocate_tcp_port()
    endpoint = f"tcp:{transitport}:interface=127.0.0.1"
    ep = endpoints.serverFromString(reactor, endpoint)
    usage = create_usage_tracker(blur_usage=None, log_file=None, usage_db=None)
    transit_server = ServerFactory()
    transit_server.protocol = TransitConnection
    transit_server.log_requests = False
    transit_server.transit = Transit(usage, reactor.seconds)
    service = StreamServerEndpointService(ep, transit_server)
    pytest_twisted.blockon(service.startService())
    yield endpoint
    pytest_twisted.blockon(service.stopService())
