import random
from unittest import mock

import pytest
from pytest_twisted import ensureDeferred
from twisted.internet import defer, endpoints, protocol
from twisted.internet.error import CannotListenError
from twisted.internet.interfaces import IStreamServerEndpoint
from zope.interface.verify import verifyObject

from .. import _listen
from ..transit import allocate_tcp_port


@pytest.mark.parametrize("value, expected", [
    ("50000-50010", (50000, 50010)),
    (" 50000 - 50010 ", (50000, 50010)),
    ("50000", (50000, 50000)),
    ("1-65535", (1, 65535)),
])
def test_parse_port_range(value, expected):
    assert _listen.parse_port_range(value) == expected


@pytest.mark.parametrize("value", [
    "", "-", "nope", "50000-", "-50000", "1-2-3", "50010-50000", "0", "0-10",
    "65536", "1-65536",
])
def test_parse_port_range_invalid(value):
    with pytest.raises(ValueError):
        _listen.parse_port_range(value)


def test_candidate_ports_small_range():
    # a range smaller than the number of attempts is tried completely
    ports = _listen.candidate_ports((6000, 6002), attempts=5)
    assert sorted(ports) == [6000, 6001, 6002]


def test_candidate_ports_large_range():
    rng = random.Random(1)
    ports = _listen.candidate_ports((10000, 60000), attempts=5, rng=rng)
    assert len(ports) == 5
    assert len(set(ports)) == 5
    assert all(10000 <= p <= 60000 for p in ports)


def test_endpoint_interface():
    ep = _listen.PortRangeServerEndpoint(mock.Mock(), (6000, 6010))
    assert verifyObject(IStreamServerEndpoint, ep)


def _fake_endpoints(results):
    """
    Patch serverFromString to return endpoints whose listen() gives the
    next of ``results`` (an exception to fail with, or a value).
    """
    calls = []

    def server_from_string(reactor, desc):
        calls.append(desc)
        ep = mock.Mock()
        result = results.pop(0)
        if isinstance(result, Exception):
            ep.listen = mock.Mock(return_value=defer.fail(result))
        else:
            ep.listen = mock.Mock(return_value=defer.succeed(result))
        return ep
    patcher = mock.patch("wormhole._listen.serverFromString",
                         side_effect=server_from_string)
    return patcher, calls


def test_retry_until_free():
    lp = object()
    patcher, calls = _fake_endpoints([
        CannotListenError("", 6000, OSError("in use")),
        CannotListenError("", 6001, OSError("in use")),
        lp,
    ])
    with patcher, mock.patch("wormhole._listen.candidate_ports",
                             return_value=[6000, 6001, 6002]):
        d = _listen.listen_in_port_range(None, mock.Mock(), (6000, 6002))
    assert calls == ["tcp:6000", "tcp:6001", "tcp:6002"]
    assert d.result is lp


def test_give_up():
    patcher, calls = _fake_endpoints([
        CannotListenError("", 6000, OSError("in use")),
        CannotListenError("", 6001, OSError("in use")),
    ])
    with patcher, mock.patch("wormhole._listen.candidate_ports",
                             return_value=[6000, 6001]):
        d = _listen.listen_in_port_range(None, mock.Mock(), (6000, 6001))
    assert calls == ["tcp:6000", "tcp:6001"]
    f = d.result
    d.addErrback(lambda _: None)  # we checked it
    assert f.check(CannotListenError)
    assert "6000-6001" in str(f.value)
    assert "tried 2 port(s)" in str(f.value)


def test_other_errors_are_not_retried():
    # only "can't listen on this port" is worth another try
    patcher, calls = _fake_endpoints([ValueError("boom"), object()])
    with patcher, mock.patch("wormhole._listen.candidate_ports",
                             return_value=[6000, 6001]):
        d = _listen.listen_in_port_range(None, mock.Mock(), (6000, 6001))
    assert calls == ["tcp:6000"]
    f = d.result
    d.addErrback(lambda _: None)  # we checked it
    assert f.check(ValueError)


@ensureDeferred
async def test_listen_real_port(reactor):
    port = allocate_tcp_port()
    ep = _listen.PortRangeServerEndpoint(reactor, (port, port))
    lp = await ep.listen(protocol.Factory.forProtocol(protocol.Protocol))
    try:
        assert lp.getHost().port == port
    finally:
        await lp.stopListening()


@ensureDeferred
async def test_listen_real_port_taken(reactor):
    # something else is already on the only port we may use
    port = allocate_tcp_port()
    factory = protocol.Factory.forProtocol(protocol.Protocol)
    other = await endpoints.serverFromString(reactor, f"tcp:{port}").listen(factory)
    try:
        ep = _listen.PortRangeServerEndpoint(reactor, (port, port))
        with pytest.raises(CannotListenError):
            await ep.listen(factory)
    finally:
        await other.stopListening()
