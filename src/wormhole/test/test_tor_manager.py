from __future__ import print_function, unicode_literals

import io

from twisted.internet import defer
from twisted.internet.error import ConnectError
from twisted.trial import unittest

import mock

from .._interfaces import ITorManager
from ..errors import NoTorError
from ..tor_manager import SocksOnlyTor, get_tor


class X():
    pass


class Tor(unittest.TestCase):
    def test_no_txtorcon(self):
        with mock.patch("wormhole.tor_manager.txtorcon", None):
            self.failureResultOf(get_tor(None), NoTorError)

    def test_bad_args(self):
        f = self.failureResultOf(
            get_tor(None, launch_tor="not boolean"), TypeError)
        self.assertEqual(str(f.value), "launch_tor= must be boolean")

        f = self.failureResultOf(
            get_tor(None, tor_control_port=1234), TypeError)
        self.assertEqual(str(f.value), "tor_control_port= must be str or None")
        f = self.failureResultOf(
            get_tor(
                None, launch_tor=True, tor_control_port="tcp:127.0.0.1:1234"),
            ValueError)
        self.assertEqual(
            str(f.value),
            "cannot combine --launch-tor and --tor-control-port=")

    def test_launch(self):
        reactor = object()
        my_tor = X()  # object() didn't like providedBy()
        launch_d = defer.Deferred()
        stderr = io.StringIO()
        with mock.patch(
                "wormhole.tor_manager.txtorcon.launch",
                side_effect=launch_d) as launch:
            d = get_tor(reactor, launch_tor=True, stderr=stderr)
            self.assertNoResult(d)
            self.assertEqual(launch.mock_calls, [mock.call(reactor)])
            launch_d.callback(my_tor)
            tor = self.successResultOf(d)
            self.assertIs(tor, my_tor)
            self.assert_(ITorManager.providedBy(tor))
            self.assertEqual(
                stderr.getvalue(),
                " launching a new Tor process, this may take a while..\n")

    def test_connect(self):
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
        self.assertEqual(sfs.mock_calls, [])
        self.assertNoResult(d)
        self.assertEqual(connect.mock_calls, [mock.call(reactor)])
        connect_d.callback(my_tor)
        tor = self.successResultOf(d)
        self.assertIs(tor, my_tor)
        self.assert_(ITorManager.providedBy(tor))
        self.assertEqual(stderr.getvalue(),
                         " using Tor via default control port\n")

    def test_connect_fails(self):
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
        self.assertEqual(sfs.mock_calls, [])
        self.assertNoResult(d)
        self.assertEqual(connect.mock_calls, [mock.call(reactor)])

        connect_d.errback(ConnectError())
        tor = self.successResultOf(d)
        self.assertIsInstance(tor, SocksOnlyTor)
        self.assert_(ITorManager.providedBy(tor))
        self.assertEqual(tor._reactor, reactor)
        self.assertEqual(
            stderr.getvalue(),
            " unable to find default Tor control port, using SOCKS\n")

    def test_connect_custom_control_port(self):
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
        self.assertEqual(sfs.mock_calls, [mock.call(reactor, tcp)])
        self.assertNoResult(d)
        self.assertEqual(connect.mock_calls, [mock.call(reactor, ep)])
        connect_d.callback(my_tor)
        tor = self.successResultOf(d)
        self.assertIs(tor, my_tor)
        self.assert_(ITorManager.providedBy(tor))
        self.assertEqual(stderr.getvalue(),
                         " using Tor via control port at PORT\n")

    def test_connect_custom_control_port_fails(self):
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
        self.assertEqual(sfs.mock_calls, [mock.call(reactor, tcp)])
        self.assertNoResult(d)
        self.assertEqual(connect.mock_calls, [mock.call(reactor, ep)])

        connect_d.errback(ConnectError())
        self.failureResultOf(d, ConnectError)
        self.assertEqual(stderr.getvalue(), "")


class SocksOnly(unittest.TestCase):
    def test_tor(self):
        reactor = object()
        sot = SocksOnlyTor(reactor)
        fake_ep = object()
        with mock.patch(
                "wormhole.tor_manager.txtorcon.TorClientEndpoint",
                return_value=fake_ep) as tce:
            ep = sot.stream_via("host", "port")
        self.assertIs(ep, fake_ep)
        self.assertEqual(tce.mock_calls, [
            mock.call(
                "host",
                "port",
                socks_endpoint=None,
                tls=False,
                reactor=reactor)
        ])
