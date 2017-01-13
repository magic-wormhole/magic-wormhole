from __future__ import print_function, unicode_literals
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.error import ConnectError
from twisted.internet.endpoints import clientFromString
import txtorcon
import ipaddress
from .timing import DebugTiming
from .transit import allocate_tcp_port

class TorManager:
    def __init__(self, reactor, launch_tor=False, tor_control_port=None,
                 timing=None):
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
        assert isinstance(launch_tor, int) # note: False is int
        assert isinstance(tor_control_port, (type(""), type(None)))
        if launch_tor and tor_control_port is not None:
            raise ValueError("cannot combine --launch-tor and --tor-control-port=")
        self._launch_tor = launch_tor
        self._tor_control_port = tor_control_port
        self._timing = timing or DebugTiming()

    @inlineCallbacks
    def start(self):
        # Connect to an existing Tor, or create a new one. If we need to
        # launch an onion service, then we need a working control port (and
        # authentication cookie). If we're only acting as a client, we don't
        # need the control port.

        if self._launch_tor:
            print("launching my own Tor process")
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
                        break
                else:
                    tproto = None
                    tconfig = None
                    socks_desc = "tcp:127.0.0.1:9050" # fallback

        self._tor_protocol = tproto
        self._tor_config = tconfig
        self._tor_socks_endpoint = clientFromString(self._reactor, socks_desc)

    @inlineCallbacks
    def _do_launch_tor(self):
        tconfig = txtorcon.TorConfig()
        #tconfig.ControlPort = allocate_tcp_port() # defaults to 9052
        #print("setting config.ControlPort to", tconfig.ControlPort)
        tconfig.SocksPort = allocate_tcp_port()
        socks_desc = "tcp:127.0.0.1:%d" % tconfig.SocksPort
        #print("setting config.SocksPort to", tconfig.SocksPort)

        # this could take tor_binary=
        tproto = yield txtorcon.launch_tor(tconfig, self._reactor)
        returnValue((tproto, tconfig, socks_desc))

    @inlineCallbacks
    def _try_control_port(self, control_port):
        NOPE = (None, None, None)
        ep = clientFromString(self._reactor, control_port)
        try:
            tproto = yield txtorcon.build_tor_connection(ep, build_state=False)
            # now wait for bootstrap
            tconfig = yield txtorcon.TorConfig.from_protocol(tproto)
        except (ValueError, ConnectError):
            returnValue(NOPE)
        socks_ports = list(tconfig.SocksPort)
        for socks_port in socks_ports:
            pieces = socks_port.split()
            p = pieces[0]
            if p == txtorcon.DEFAULT_VALUE:
                p = "9050"
            try:
                portnum = int(p)
                socks_desc = "tcp:127.0.0.1:%d" % portnum
                returnValue((tproto, tconfig, socks_desc))
            except ValueError:
                pass
        print("connected to Tor, but could not use config.SocksPort: %r" %
              (socks_ports,))
        returnValue(NOPE)

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
            print("ignoring non-Tor-able %s" % host)
            return None

        # txsocksx doesn't like unicode: it concatenates some binary protocol
        # bytes with the hostname when talking to the SOCKS server, so the
        # py2 automatic unicode promotion blows up
        host = host.encode("ascii")
        ep = txtorcon.TorClientEndpoint(host, port,
                                        socks_endpoint=self._tor_socks_endpoint)
        return ep
