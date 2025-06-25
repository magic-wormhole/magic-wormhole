from twisted.internet.defer import ensureDeferred
from twisted.python import log

import pytest
import pytest_twisted

from .common import setup_mailbox, setup_transit_relay

# from kyle altendorf
# see also https://github.com/pytest-dev/pytest-twisted/issues/4
import gc


@pytest.fixture(scope="session")
def reactor():
    from twisted.internet import reactor
    yield reactor


@pytest.fixture(scope="session")
def mailbox(reactor):
    from wormhole import __version__
    mb = pytest_twisted.blockon(
        ensureDeferred(
            setup_mailbox(reactor, advertise_version=str(__version__))
        )
    )
    mb.service.startService()
    yield mb
    pytest_twisted.blockon(mb.service.stopService())
    pytest_twisted.blockon(mb.port.stopListening())
    ##from twisted.internet import task
    ##pytest_twisted.blockon(task.deferLater(reactor, 0.1, lambda: None))


@pytest.fixture(scope="session")
def transit_relay(reactor):
    url, service = setup_transit_relay(reactor)
    service.startService()
    yield url
    pytest_twisted.blockon(service.stopService())





class Observer:
    def __init__(self):
        self.failures = []

    def __call__(self, event_dict):
        is_error = event_dict.get('isError')
        if is_error:
            self.failures.append(event_dict["failure"])

    def flush(self, klass):
        flushed = [
            f
            for f in self.failures
            if isinstance(f.value, klass)
        ]
        self.failures = [
            f
            for f in self.failures
            if not isinstance(f.value, klass)
        ]
        return flushed

    def assert_empty(self):
        assert [] == self.failures


@pytest.fixture
def observe_errors():
    observer = Observer()
    gc.collect()
    log.startLoggingWithObserver(observer, 0)

    yield observer

    gc.collect()
    log.removeObserver(observer)
    observer.assert_empty()
