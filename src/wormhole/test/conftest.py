from twisted.internet.defer import ensureDeferred
from twisted.python import log

import pytest
import pytest_twisted

from .common import setup_mailbox, setup_transit_relay


@pytest.fixture(scope="session")
def reactor():
    from twisted.internet import reactor
    yield reactor


@pytest.fixture(scope="session")
def mailbox(reactor):
    mb = pytest_twisted.blockon(ensureDeferred(setup_mailbox(reactor)))
    mb.service.startService()
    yield mb
    pytest_twisted.blockon(mb.service.stopService())
    mb.site.stopFactory()


@pytest.fixture(scope="session")
def transit_relay(reactor):
    url, service = setup_transit_relay(reactor)
    service.startService()
    yield url
    pytest_twisted.blockon(service.stopService())



# from kyle altendorf
# see also https://github.com/pytest-dev/pytest-twisted/issues/4
import gc
import twisted.logger


class Observer:
    def __init__(self):
        self.failures = []

    def __call__(self, event_dict):
        is_error = event_dict.get('isError')
        s = 'Unhandled error in Deferred'.casefold()
        is_unhandled = s in event_dict.get('log_format', '').casefold()

        if is_error and is_unhandled:
            self.failures.append(event_dict)

    def flush(self, klass):
        for f in self.failures:
            print(f, klass)

    def assert_empty(self):
        assert [] == self.failures


@pytest.fixture
def observe_errors():
    observer = Observer()
    log.startLoggingWithObserver(observer, 0)

    yield observer

    gc.collect()
    log.removeObserver(observer)
    observer.assert_empty()
