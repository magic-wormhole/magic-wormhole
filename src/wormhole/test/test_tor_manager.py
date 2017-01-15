from __future__ import print_function, unicode_literals
import mock, io
from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.error import ConnectError
from six import next

from ..tor_manager import TorManager, DEFAULT_VALUE

class Tor(unittest.TestCase):
    def test_create(self):
        tm = TorManager(None)
        del tm

    def test_bad_args(self):
        e = self.assertRaises(TypeError,
                              TorManager, None, launch_tor="not boolean")
        self.assertEqual(str(e), "launch_tor= must be boolean")
        e = self.assertRaises(TypeError,
                              TorManager, None, tor_control_port=1234)
        self.assertEqual(str(e), "tor_control_port= must be str or None")
        e = self.assertRaises(ValueError,
                              TorManager, None, launch_tor=True,
                              tor_control_port="tcp:127.0.0.1:1234")
        self.assertEqual(str(e),
                         "cannot combine --launch-tor and --tor-control-port=")

    def test_start_launch_tor(self):
        reactor = object()
        stderr = io.StringIO()
        tm = TorManager(reactor, launch_tor=True, stderr=stderr)
        dlt_d = defer.Deferred()
        tm._do_launch_tor = mock.Mock(return_value=dlt_d)
        tm._try_control_port = mock.Mock()
        d = tm.start()
        self.assertNoResult(d)
        tsep = object()
        with mock.patch("wormhole.tor_manager.clientFromString",
                        return_value=tsep) as cfs:
            dlt_d.callback(("tproto", "tconfig", "socks_desc"))
            res = self.successResultOf(d)
            self.assertEqual(res, None)
            self.assertEqual(tm._tor_protocol, "tproto")
            self.assertEqual(tm._tor_config, "tconfig")
            self.assertEqual(tm._tor_socks_endpoint, tsep)
            self.assertEqual(tm._do_launch_tor.mock_calls, [mock.call()])
            self.assertEqual(tm._try_control_port.mock_calls, [])
            self.assertEqual(cfs.mock_calls, [mock.call(reactor, "socks_desc")])

    def test_start_control_port_default_failure(self):
        reactor = object()
        stderr = io.StringIO()
        tm = TorManager(reactor, stderr=stderr)
        tm._do_launch_tor = mock.Mock()
        tcp_ds = [defer.Deferred() for i in range(5)]
        tcp_ds_iter = iter(tcp_ds)
        attempted_control_ports = []
        def next_d(control_port):
            attempted_control_ports.append(control_port)
            return next(tcp_ds_iter)
        tm._try_control_port = mock.Mock(side_effect=next_d)
        d = tm.start()
        tsep = object()
        with mock.patch("wormhole.tor_manager.clientFromString",
                        return_value=tsep) as cfs:
            self.assertNoResult(d)
            self.assertEqual(attempted_control_ports,
                             ["unix:/var/run/tor/control"])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call("unix:/var/run/tor/control")])
            tcp_ds[0].callback((None, None, None))

            self.assertNoResult(d)
            self.assertEqual(attempted_control_ports,
                             ["unix:/var/run/tor/control",
                              "tcp:127.0.0.1:9051",
                              ])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call("unix:/var/run/tor/control"),
                              mock.call("tcp:127.0.0.1:9051"),
                              ])
            tcp_ds[1].callback((None, None, None))

            self.assertNoResult(d)
            self.assertEqual(attempted_control_ports,
                             ["unix:/var/run/tor/control",
                              "tcp:127.0.0.1:9051",
                              "tcp:127.0.0.1:9151",
                              ])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call("unix:/var/run/tor/control"),
                              mock.call("tcp:127.0.0.1:9051"),
                              mock.call("tcp:127.0.0.1:9151"),
                              ])
            tcp_ds[2].callback((None, None, None))

            res = self.successResultOf(d)
            self.assertEqual(res, None)
            self.assertEqual(tm._tor_protocol, None)
            self.assertEqual(tm._tor_config, None)
            self.assertEqual(tm._tor_socks_endpoint, tsep)
            self.assertEqual(tm._do_launch_tor.mock_calls, [])
            self.assertEqual(cfs.mock_calls,
                             [mock.call(reactor, "tcp:127.0.0.1:9050")])

    def test_start_control_port_default(self):
        reactor = object()
        stderr = io.StringIO()
        tm = TorManager(reactor, stderr=stderr)
        tm._do_launch_tor = mock.Mock()
        tcp_d = defer.Deferred()
        # let it succeed on the first try
        tm._try_control_port = mock.Mock(return_value=tcp_d)
        d = tm.start()
        self.assertNoResult(d)
        tsep = object()
        with mock.patch("wormhole.tor_manager.clientFromString",
                        return_value=tsep) as cfs:
            tcp_d.callback(("tproto", "tconfig", "socks_desc"))
            res = self.successResultOf(d)
            self.assertEqual(res, None)
            self.assertEqual(tm._tor_protocol, "tproto")
            self.assertEqual(tm._tor_config, "tconfig")
            self.assertEqual(tm._tor_socks_endpoint, tsep)
            self.assertEqual(tm._do_launch_tor.mock_calls, [])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call("unix:/var/run/tor/control")])
            self.assertEqual(cfs.mock_calls, [mock.call(reactor, "socks_desc")])

    def test_start_control_port_non_default_failure(self):
        reactor = object()
        my_port = "my_port"
        stderr = io.StringIO()
        tm = TorManager(reactor, tor_control_port=my_port, stderr=stderr)
        tm._do_launch_tor = mock.Mock()
        tcp_ds = [defer.Deferred() for i in range(5)]
        tcp_ds_iter = iter(tcp_ds)
        attempted_control_ports = []
        def next_d(control_port):
            attempted_control_ports.append(control_port)
            return next(tcp_ds_iter)
        tm._try_control_port = mock.Mock(side_effect=next_d)
        d = tm.start()
        tsep = object()
        with mock.patch("wormhole.tor_manager.clientFromString",
                        return_value=tsep) as cfs:
            self.assertNoResult(d)
            self.assertEqual(attempted_control_ports, [my_port])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call(my_port)])
            tcp_ds[0].callback((None, None, None))

            res = self.successResultOf(d)
            self.assertEqual(res, None)
            self.assertEqual(tm._tor_protocol, None)
            self.assertEqual(tm._tor_config, None)
            self.assertEqual(tm._tor_socks_endpoint, tsep)
            self.assertEqual(tm._do_launch_tor.mock_calls, [])
            self.assertEqual(cfs.mock_calls,
                             [mock.call(reactor, "tcp:127.0.0.1:9050")])

    def test_start_control_port_non_default(self):
        reactor = object()
        my_port = "my_port"
        stderr = io.StringIO()
        tm = TorManager(reactor, tor_control_port=my_port, stderr=stderr)
        tm._do_launch_tor = mock.Mock()
        tcp_d = defer.Deferred()
        tm._try_control_port = mock.Mock(return_value=tcp_d)
        d = tm.start()
        self.assertNoResult(d)
        tsep = object()
        with mock.patch("wormhole.tor_manager.clientFromString",
                        return_value=tsep) as cfs:
            tcp_d.callback(("tproto", "tconfig", "socks_desc"))
            res = self.successResultOf(d)
            self.assertEqual(res, None)
            self.assertEqual(tm._tor_protocol, "tproto")
            self.assertEqual(tm._tor_config, "tconfig")
            self.assertEqual(tm._tor_socks_endpoint, tsep)
            self.assertEqual(tm._do_launch_tor.mock_calls, [])
            self.assertEqual(tm._try_control_port.mock_calls,
                             [mock.call(my_port)])
            self.assertEqual(cfs.mock_calls, [mock.call(reactor, "socks_desc")])

    def test_launch(self):
        reactor = object()
        stderr = io.StringIO()
        tc = mock.Mock()
        mock_TorConfig = mock.patch("wormhole.tor_manager.TorConfig",
                                    return_value=tc)
        lt_d = defer.Deferred()
        mock_launch_tor = mock.patch("wormhole.tor_manager.launch_tor",
                                     return_value=lt_d)
        mock_allocate_tcp_port = mock.patch("wormhole.tor_manager.allocate_tcp_port",
                                            return_value=12345)
        mock_clientFromString = mock.patch("wormhole.tor_manager.clientFromString")
        with mock_TorConfig as mtc:
            with mock_launch_tor as mlt:
                with mock_allocate_tcp_port as matp:
                    with mock_clientFromString as mcfs:
                        tm = TorManager(reactor, launch_tor=True, stderr=stderr)
                        d = tm.start()
                        self.assertNoResult(d)
                        tp = mock.Mock()
                        lt_d.callback(tp)
        res = self.successResultOf(d)
        self.assertEqual(res, None)
        self.assertIs(tm._tor_protocol, tp)
        self.assertIs(tm._tor_config, tc)
        self.assertEqual(mtc.mock_calls, [mock.call()])
        self.assertEqual(mlt.mock_calls, [mock.call(tc, reactor)])
        self.assertEqual(matp.mock_calls, [mock.call()])
        self.assertEqual(mcfs.mock_calls,
                         [mock.call(reactor, "tcp:127.0.0.1:12345")])

    def _do_test_try_control_port(self, socks_ports, exp_socks_desc,
                                  btc_exception=None, tcfp_exception=None):
        reactor = object()
        stderr = io.StringIO()
        ep = object()
        mock_clientFromString = mock.patch("wormhole.tor_manager.clientFromString",
                                           return_value=ep)
        tproto = mock.Mock()
        btc_d = defer.Deferred()
        mock_build_tor_connection = mock.patch("wormhole.tor_manager.build_tor_connection", return_value=btc_d)
        torconfig = mock.Mock()
        tc = mock.Mock()
        tc.SocksPort = iter(socks_ports)
        tc_d = defer.Deferred()
        torconfig.from_protocol = mock.Mock(return_value=tc_d)
        mock_torconfig = mock.patch("wormhole.tor_manager.TorConfig", torconfig)

        control_port = object()

        with mock_clientFromString as cfs:
            with mock_build_tor_connection as btc:
                with mock_torconfig:
                    tm = TorManager(reactor, stderr=stderr)
                    d = tm._try_control_port(control_port)
                    # waiting in 'tproto = yield build_tor_connection(..)'
                    self.assertNoResult(d)
                    self.assertEqual(cfs.mock_calls,
                                     [mock.call(reactor, control_port)])
                    self.assertEqual(btc.mock_calls,
                                     [mock.call(ep, build_state=False)])
                    self.assertEqual(torconfig.from_protocol.mock_calls, [])

                    btc_d.callback(tproto)
                    # waiting in 'tconfig = yield TorConfig.from_protocol(..)'
                    self.assertNoResult(d)
                    self.assertEqual(torconfig.from_protocol.mock_calls,
                                     [mock.call(tproto)])

                    tc_d.callback(tc)
                    res = self.successResultOf(d)
                    self.assertEqual(res, (tproto, tc, exp_socks_desc))

    def test_try_control_port(self):
        self._do_test_try_control_port(["1234 ignorestuff",
                                        "unix:/foo WorldWritable"],
                                       "tcp:127.0.0.1:1234")
        self._do_test_try_control_port(["unix:/foo WorldWritable",
                                        "1234 ignorestuff"],
                                       "unix:/foo")
        self._do_test_try_control_port([DEFAULT_VALUE,
                                        "1234"],
                                       "tcp:127.0.0.1:9050")

    def _do_test_try_control_port_exception(self, btc_exc=None, tcfp_exc=None):
        reactor = object()
        stderr = io.StringIO()
        ep = object()
        mock_clientFromString = mock.patch("wormhole.tor_manager.clientFromString",
                                           return_value=ep)
        tproto = mock.Mock()
        btc_d = defer.Deferred()
        mock_build_tor_connection = mock.patch("wormhole.tor_manager.build_tor_connection", return_value=btc_d)
        torconfig = mock.Mock()
        tcfp_d = defer.Deferred()
        torconfig.from_protocol = mock.Mock(return_value=tcfp_d)
        mock_torconfig = mock.patch("wormhole.tor_manager.TorConfig", torconfig)

        control_port = object()

        with mock_clientFromString:
            with mock_build_tor_connection:
                with mock_torconfig:
                    tm = TorManager(reactor, stderr=stderr)
                    d = tm._try_control_port(control_port)
                    # waiting in 'tproto = yield build_tor_connection(..)'
                    self.assertNoResult(d)

                    if btc_exc:
                        btc_d.errback(btc_exc)
                    else:
                        btc_d.callback(tproto)
                        assert tcfp_exc
                        tcfp_d.errback(tcfp_exc)

                    res = self.successResultOf(d)
                    self.assertEqual(res, (None, None, None))

    def test_try_control_port_error(self):
        self._do_test_try_control_port_exception(btc_exc=ValueError())
        self._do_test_try_control_port_exception(btc_exc=ConnectError())
        self._do_test_try_control_port_exception(tcfp_exc=ValueError())
        self._do_test_try_control_port_exception(tcfp_exc=ConnectError())

    def test_badaddr(self):
        tm = TorManager(None)
        isnon = tm.is_non_public_numeric_address
        self.assertTrue(isnon("10.0.0.1"))
        self.assertTrue(isnon("127.0.0.1"))
        self.assertTrue(isnon("192.168.78.254"))
        self.assertTrue(isnon("::1"))
        self.assertFalse(isnon("8.8.8.8"))
        self.assertFalse(isnon("example.org"))

    def test_endpoint(self):
        reactor = object()
        stderr = io.StringIO()
        tm = TorManager(reactor, stderr=stderr)
        tm._tor_socks_endpoint = tse = object()
        exp_ep = object()
        with mock.patch("wormhole.tor_manager.TorClientEndpoint",
                        return_value=exp_ep) as tce:
            ep = tm.get_endpoint_for("example.com", 1234)
            self.assertIs(ep, exp_ep)
            self.assertEqual(tce.mock_calls,
                             [mock.call(b"example.com", 1234,
                                        socks_endpoint=tse)])
        with mock.patch("wormhole.tor_manager.TorClientEndpoint",
                        return_value=exp_ep) as tce:
            ep = tm.get_endpoint_for("127.0.0.1", 1234)
            self.assertEqual(ep, None)
            self.assertEqual(tce.mock_calls, [])
