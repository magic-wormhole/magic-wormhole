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

from .common import setup_mailbox, setup_transit_relay


@pytest.fixture(scope="session")
def mailbox(reactor):
    mb = setup_mailbox(reactor)
    mb.service.startService()
    yield mb
    pytest_twisted.blockon(mb.service.stopService())


@pytest.fixture(scope="session")
def transit_relay(reactor):
    url, service = setup_transit_relay()
    pytest_twisted.blockon(service.startService())
    yield url
    pytest_twisted.blockon(service.stopService())
