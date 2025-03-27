import io

from twisted.internet import defer
from twisted.internet.error import ConnectError

from unittest import mock
import pytest
import pytest_twisted

from .._interfaces import ITorManager
from ..errors import NoTorError
from ..tor_manager import SocksOnlyTor, get_tor


class X():
    pass


@pytest_twisted.ensureDeferred
async def test_no_txtorcon():
    with mock.patch("wormhole.tor_manager.txtorcon", None):
        with pytest.raises(NoTorError):
            await get_tor(None)


@pytest_twisted.ensureDeferred
async def test_bad_args():
    with pytest.raises(TypeError) as f:
        await get_tor(None, launch_tor="not boolean")
    assert str(f.value) == "launch_tor= must be boolean"

    with pytest.raises(TypeError) as f:
        await get_tor(None, tor_control_port=1234)
    assert str(f.value) == "tor_control_port= must be str or None"

    with pytest.raises(ValueError) as f:
        await get_tor(None, launch_tor=True, tor_control_port="tcp:127.0.0.1:1234")
    assert str(f.value) == "cannot combine --launch-tor and --tor-control-port="


@pytest_twisted.ensureDeferred
async def test_launch():
    reactor = object()
    my_tor = X()  # object() didn't like providedBy()
    launch_d = defer.Deferred()
    stderr = io.StringIO()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.launch",
            side_effect=launch_d) as launch:
        d = get_tor(reactor, launch_tor=True, stderr=stderr)
        assert not d.called
        assert launch.mock_calls == [mock.call(reactor)]
        launch_d.callback(my_tor)
        tor = await d
        assert tor is my_tor
        assert ITorManager.providedBy(tor)
        assert stderr.getvalue() == \
            " launching a new Tor process, this may take a while..\n"


@pytest_twisted.ensureDeferred
async def test_connect():
    reactor = object()
    my_tor = X()  # object() didn't like providedBy()
    connect_d = defer.Deferred()
    stderr = io.StringIO()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.connect",
            side_effect=connect_d) as connect:
        with mock.patch(
                "wormhole.tor_manager.clientFromString",
                side_effect=["foo"]) as sfs:
            d = get_tor(reactor, stderr=stderr)
    assert sfs.mock_calls == []
    assert not d.called
    assert connect.mock_calls == [mock.call(reactor)]
    connect_d.callback(my_tor)
    tor = await d
    assert tor is my_tor
    assert ITorManager.providedBy(tor)
    assert stderr.getvalue() == \
                     " using Tor via default control port\n"


@pytest_twisted.ensureDeferred
async def test_connect_fails():
    reactor = object()
    connect_d = defer.Deferred()
    stderr = io.StringIO()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.connect",
            side_effect=connect_d) as connect:
        with mock.patch(
                "wormhole.tor_manager.clientFromString",
                side_effect=["foo"]) as sfs:
            d = get_tor(reactor, stderr=stderr)
    assert sfs.mock_calls == []
    assert not d.called
    assert connect.mock_calls == [mock.call(reactor)]

    connect_d.errback(ConnectError())
    tor = await d
    assert isinstance(tor, SocksOnlyTor)
    assert ITorManager.providedBy(tor)
    assert tor._reactor == reactor
    assert stderr.getvalue() == \
        " unable to find default Tor control port, using SOCKS\n"


@pytest_twisted.ensureDeferred
async def test_connect_custom_control_port():
    reactor = object()
    my_tor = X()  # object() didn't like providedBy()
    tcp = "PORT"
    ep = object()
    connect_d = defer.Deferred()
    stderr = io.StringIO()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.connect",
            side_effect=connect_d) as connect:
        with mock.patch(
                "wormhole.tor_manager.clientFromString",
                side_effect=[ep]) as sfs:
            d = get_tor(reactor, tor_control_port=tcp, stderr=stderr)
    assert sfs.mock_calls == [mock.call(reactor, tcp)]
    assert not d.called
    assert connect.mock_calls == [mock.call(reactor, ep)]
    connect_d.callback(my_tor)
    tor = await d
    assert tor is my_tor
    assert ITorManager.providedBy(tor)
    assert stderr.getvalue() == \
                     " using Tor via control port at PORT\n"


@pytest_twisted.ensureDeferred
async def test_connect_custom_control_port_fails():
    reactor = object()
    tcp = "port"
    ep = object()
    connect_d = defer.Deferred()
    stderr = io.StringIO()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.connect",
            side_effect=connect_d) as connect:
        with mock.patch(
                "wormhole.tor_manager.clientFromString",
                side_effect=[ep]) as sfs:
            d = get_tor(reactor, tor_control_port=tcp, stderr=stderr)
    assert sfs.mock_calls == [mock.call(reactor, tcp)]
    assert not d.called
    assert connect.mock_calls == [mock.call(reactor, ep)]

    connect_d.errback(ConnectError())
    with pytest.raises(ConnectError):
        await d
    assert stderr.getvalue() == ""


def test_tor():
    reactor = object()
    sot = SocksOnlyTor(reactor)
    fake_ep = object()
    with mock.patch(
            "wormhole.tor_manager.txtorcon.TorClientEndpoint",
            return_value=fake_ep) as tce:
        ep = sot.stream_via("host", "port")
    assert ep is fake_ep
    assert tce.mock_calls == [
        mock.call(
            "host",
            "port",
            socks_endpoint=None,
            tls=False,
            reactor=reactor)
    ]
