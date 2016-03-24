from __future__ import print_function
import time
from zope.interface import implementer
from twisted.web import error as web_error
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.compat import nativeString
from twisted.internet.error import ConnectError
from twisted.web import iweb
import txtorcon
import ipaddr
from ..timing import DebugTiming
from .transit import allocate_tcp_port

# based on twisted.web.client._StandardEndpointFactory
@implementer(iweb.IAgentEndpointFactory)
class TorWebAgentEndpointFactory(object):
    def __init__(self, reactor, socks_port):
        self._reactor = reactor
        self._socks_port = socks_port

    def endpointForURI(self, uri):
        try:
            host = nativeString(uri.host)
        except UnicodeDecodeError:
            raise ValueError(("The host of the provided URI ({uri.host!r}) "
                              "contains non-ASCII octets, it should be ASCII "
                              "decodable.").format(uri=uri))

        if uri.scheme == b'http':
            print("building URI endpoint with tor for %s" % uri.toBytes())
            return txtorcon.TorClientEndpoint(#self._reactor,
                host, uri.port,
                socks_hostname="127.0.0.1", socks_port=self._socks_port)
        elif uri.scheme == b'https':
            raise NotImplementedError
            # find some twisted thing that wraps a normal
            # IStreamClientEndpoint in a TLS-ifying layer, and wrap it around
            # a TorClientEndpoint. Maybe t.i.endpoints.wrapClientTLS
        else:
            raise web_error.SchemeNotSupported("Unsupported scheme: %r" % (uri.scheme,))

class TorManager:
    def __init__(self, reactor, tor_socks_port=None, tor_control_port=9051,
                 timing=None):
        """
        If tor_socks_port= is provided, I will assume that it points to a
        functioning SOCKS server, and will use it for all outbound
        connections. I will not attempt to establish a control-port
        connection, and I will not be able to run a server.

        Otherwise, I will try to connect to an existing Tor process, first on
        localhost:9051, then /var/run/tor/control. Then I will try to
        authenticate, by reading a cookie file named by the Tor process. This
        will succeed if 1: Tor is already running, and 2: the current user
        can read that file (either they started it, e.g. TorBrowser, or they
        are in a unix group that's been given access, e.g. debian-tor).

        If tor_control_port= is provided, I will use it instead of 9051.
        """
        self._reactor = reactor
        # note: False is int
        assert isinstance(tor_socks_port, (int, type(None)))
        assert isinstance(tor_control_port, int)
        self._tor_socks_port = tor_socks_port
        self._tor_control_port = tor_control_port
        self._timing = timing or DebugTiming()

    @inlineCallbacks
    def start(self):
        # Connect to an existing Tor, or create a new one. If we need to
        # launch an onion service, then we need a working control port (and
        # authentication cookie). If we're only acting as a client, we don't
        # need the control port.

        if self._tor_socks_port is not None:
            self._can_run_service = False
            returnValue(True)

        _start_find = self._timing.add_event("find tor")
        # try port 9051, then try /var/run/tor/control . Throws on failure.
        state = None
        _start_tcp = self._timing.add_event("tor localhost")
        try:
            connection = (self._reactor, "127.0.0.1", self._tor_control_port)
            state = yield txtorcon.build_tor_connection(connection)
            self._tor_protocol = state.protocol
        except ConnectError:
            print("unable to reach Tor on %d" % self._tor_control_port)
            pass
        self._timing.finish_event(_start_tcp)

        if not state:
            _start_unix = self._timing.add_event("tor unix")
            try:
                connection = (self._reactor, "/var/run/tor/control")
                # add build_state=False to get back a Protocol object instead
                # of a State object
                state = yield txtorcon.build_tor_connection(connection)
                self._tor_protocol = state.protocol
            except (ValueError, ConnectError):
                print("unable to reach Tor on /var/run/tor/control")
                pass
            self._timing.finish_event(_start_unix)

        if state:
            print("connected to pre-existing Tor process")
            print("state:", state)
        else:
            print("launching my own Tor process")
            yield self._create_my_own_tor()
            # that sets self._tor_socks_port and self._tor_protocol

        self._timing.finish_event(_start_find)
        self._can_run_service = True
        returnValue(True)

    @inlineCallbacks
    def _create_my_own_tor(self):
        _start_launch = self._timing.add_event("launch tor")
        start = time.time()
        config = self.config = txtorcon.TorConfig()
        if 0:
            # The default is for launch_tor to create a tempdir itself, and
            # delete it when done. We only need to set a DataDirectory if we
            # want it to be persistent.
            import tempfile
            datadir = tempfile.mkdtemp()
            config.DataDirectory = datadir

        #config.ControlPort = allocate_tcp_port() # defaults to 9052
        #print("setting config.ControlPort to", config.ControlPort)
        config.SocksPort = allocate_tcp_port()
        self._tor_socks_port = config.SocksPort
        print("setting config.SocksPort to", config.SocksPort)

        tpp = yield txtorcon.launch_tor(config, self._reactor,
                                        #tor_binary=
                                        )
        # gives a TorProcessProtocol with .tor_protocol
        self._tor_protocol = tpp.tor_protocol
        print("tp:", self._tor_protocol)
        print("elapsed:", time.time() - start)
        self._timing.finish_event(_start_launch)
        returnValue(True)

    def get_web_agent_endpoint_factory(self):
        return TorWebAgentEndpointFactory(self._reactor, self._tor_socks_port)

    def is_non_public_numeric_address(self, host):
        # for numeric hostnames, skip RFC1918 addresses, since no Tor exit
        # node will be able to reach those. Likewise ignore IPv6 addresses.
        try:
            a = ipaddr.IPAddress(host)
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
                                        socks_hostname="127.0.0.1",
                                        socks_port=self._tor_socks_port)
        return ep
