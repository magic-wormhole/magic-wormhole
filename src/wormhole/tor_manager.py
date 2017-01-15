from __future__ import print_function, unicode_literals
import sys, re
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.error import ConnectError
from twisted.internet.endpoints import clientFromString
try:
    from txtorcon import (TorConfig, launch_tor, build_tor_connection,
                          DEFAULT_VALUE, TorClientEndpoint)
except ImportError:
    TorConfig = None
    launch_tor = None
    build_tor_connection = None
    TorClientEndpoint = None
    DEFAULT_VALUE = "DEFAULT_VALUE"
import ipaddress
from .timing import DebugTiming
from .transit import allocate_tcp_port

class TorManager:
    def __init__(self, reactor, launch_tor=False, tor_control_port=None,
                 timing=None, stderr=sys.stderr):
        """
        If launch_tor=True, I will try to launch a new Tor process, ask it
        for its SOCKS and control ports, and use those for outbound
        connections (and inbound onion-service listeners, if necessary).

        Otherwise if tor_control_port is provided, I will attempt to connect
        to an existing Tor's control port at the endpoint it specifies. I'll
        ask that Tor for its SOCKS port.

        With no arguments, I will try to connect to an existing Tor's control
        port at the usual places: [unix:/var/run/tor/control,
        tcp:127.0.0.1:9051, tcp:127.0.0.1:9151]. If any are successful, I'll
        ask that Tor for its SOCKS port. If none are successful, I'll attempt
        to do SOCKS to tcp:127.0.0.1:9050.

        If I am unable to make a SOCKS connection, the initial connection to
        the Rendezvous Server will fail, and the program will terminate.

        Control-port connections can only succeed if I can authenticate (by
        reading a cookie file named by the Tor process), so the current user
        must have permission to read that file (either they started Tor, e.g.
        TorBrowser, or they are in a unix group that's been given access,
        e.g. debian-tor).
        """
        # rationale: launching a new Tor takes a long time, so only do it if
        # the user specifically asks for it with --launch-tor. Using an
        # existing Tor should be much faster, but still requires general
        # permission via --tor.

        self._reactor = reactor
        if not isinstance(launch_tor, bool): # note: False is int
            raise TypeError("launch_tor= must be boolean")
        if not isinstance(tor_control_port, (type(""), type(None))):
            raise TypeError("tor_control_port= must be str or None")
        if launch_tor and tor_control_port is not None:
            raise ValueError("cannot combine --launch-tor and --tor-control-port=")
        self._launch_tor = launch_tor
        self._tor_control_port = tor_control_port
        self._timing = timing or DebugTiming()
        self._stderr = stderr

    def tor_available(self):
        # unit tests mock out everything we get from txtorcon, so we can test
        # this class under py3 even if txtorcon isn't installed. But the real
        # commands need to know if they have Tor or not.
        return bool(TorConfig)

    @inlineCallbacks
    def start(self):
        # Connect to an existing Tor, or create a new one. If we need to
        # launch an onion service, then we need a working control port (and
        # authentication cookie). If we're only acting as a client, we don't
        # need the control port.

        if self._launch_tor:
            print(" launching a new Tor process, this may take a while..",
                  file=self._stderr)
            with self._timing.add("launch tor"):
                (tproto, tconfig, socks_desc) = yield self._do_launch_tor()
        else:
            control_ports = ["unix:/var/run/tor/control", # debian tor package
                             "tcp:127.0.0.1:9051", # standard Tor
                             "tcp:127.0.0.1:9151", # TorBrowser
                             ]
            if self._tor_control_port:
                control_ports = [self._tor_control_port]
            with self._timing.add("find tor"):
                for control_port in control_ports:
                    (tproto, tconfig,
                     socks_desc) = yield self._try_control_port(control_port)
                    if tproto:
                        print(" using Tor (control port %s) (SOCKS port %s)"
                              % (control_port, socks_desc),
                              file=self._stderr)
                        break
                else:
                    tproto = None
                    tconfig = None
                    socks_desc = "tcp:127.0.0.1:9050" # fallback
                    print(" using Tor (SOCKS port %s)" % socks_desc,
                          file=self._stderr)

        self._tor_protocol = tproto
        self._tor_config = tconfig
        self._tor_socks_endpoint = clientFromString(self._reactor, socks_desc)

    @inlineCallbacks
    def _do_launch_tor(self):
        tconfig = TorConfig()
        #tconfig.ControlPort = allocate_tcp_port() # defaults to 9052
        tconfig.SocksPort = allocate_tcp_port()
        socks_desc = "tcp:127.0.0.1:%d" % tconfig.SocksPort
        # this could take tor_binary=
        tproto = yield launch_tor(tconfig, self._reactor)
        returnValue((tproto, tconfig, socks_desc))

    @inlineCallbacks
    def _try_control_port(self, control_port):
        NOPE = (None, None, None)
        ep = clientFromString(self._reactor, control_port)
        try:
            tproto = yield build_tor_connection(ep, build_state=False)
            # now wait for bootstrap
            tconfig = yield TorConfig.from_protocol(tproto)
        except (ValueError, ConnectError):
            returnValue(NOPE)
        socks_ports = list(tconfig.SocksPort)
        socks_port = socks_ports[0] # TODO: when might there be multiple?
        # I've seen "9050", and "unix:/var/run/tor/socks WorldWritable"
        pieces = socks_port.split()
        p = pieces[0]
        if p == DEFAULT_VALUE:
            socks_desc = "tcp:127.0.0.1:9050"
        elif re.search('^\d+$', p):
            socks_desc = "tcp:127.0.0.1:%s" % p
        else:
            socks_desc = p
        returnValue((tproto, tconfig, socks_desc))

    def is_non_public_numeric_address(self, host):
        # for numeric hostnames, skip RFC1918 addresses, since no Tor exit
        # node will be able to reach those. Likewise ignore IPv6 addresses.
        try:
            a = ipaddress.ip_address(host)
        except ValueError:
            return False # non-numeric, let Tor try it
        if a.version != 4:
            return True # IPv6 gets ignored
        if (a.is_loopback or a.is_multicast or a.is_private or a.is_reserved
            or a.is_unspecified):
            return True # too weird, don't connect
        return False

    def get_endpoint_for(self, host, port):
        assert isinstance(port, int)
        if self.is_non_public_numeric_address(host):
            return None

        # txsocksx doesn't like unicode: it concatenates some binary protocol
        # bytes with the hostname when talking to the SOCKS server, so the
        # py2 automatic unicode promotion blows up
        host = host.encode("ascii")
        ep = TorClientEndpoint(host, port,
                               socks_endpoint=self._tor_socks_endpoint)
        return ep
