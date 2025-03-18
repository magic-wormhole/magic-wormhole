from twisted.internet import endpoints

from wormhole_mailbox_server.database import create_channel_db, create_usage_db
from wormhole_mailbox_server.server import make_server
from wormhole_mailbox_server.web import make_web_server

import pytest
import pytest_twisted


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

