from __future__ import print_function, unicode_literals

import sys

from attr import attrib, attrs
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.endpoints import clientFromString
from zope.interface.declarations import directlyProvides

from . import _interfaces, errors
from .timing import DebugTiming

try:
    import txtorcon
except ImportError:
    txtorcon = None


@attrs
class SocksOnlyTor(object):
    _reactor = attrib()

    def stream_via(self, host, port, tls=False):
        return txtorcon.TorClientEndpoint(
            host,
            port,
            socks_endpoint=None,  # tries localhost:9050 and 9150
            tls=tls,
            reactor=self._reactor,
        )


@inlineCallbacks
def get_tor(reactor,
            launch_tor=False,
            tor_control_port=None,
            timing=None,
            stderr=sys.stderr):
    """
    If launch_tor=True, I will try to launch a new Tor process, ask it
    for its SOCKS and control ports, and use those for outbound
    connections (and inbound onion-service listeners, if necessary).

    Otherwise if tor_control_port is provided, I will attempt to connect
    to an existing Tor's control port at the endpoint it specifies.  I'll
    ask that Tor for its SOCKS port.

    With no arguments, I will try to connect to an existing Tor's control
    port at the usual places: [unix:/var/run/tor/control,
    tcp:127.0.0.1:9051, tcp:127.0.0.1:9151].  If any are successful, I'll
    ask that Tor for its SOCKS port.  If none are successful, I'll
    attempt to do SOCKS to the usual places: [tcp:127.0.0.1:9050,
    tcp:127.0.0.1:9150].

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

    if not txtorcon:
        raise errors.NoTorError()

    if not isinstance(launch_tor, bool):  # note: False is int
        raise TypeError("launch_tor= must be boolean")
    if not isinstance(tor_control_port, (type(""), type(None))):
        raise TypeError("tor_control_port= must be str or None")
    assert tor_control_port != ""
    if launch_tor and tor_control_port is not None:
        raise ValueError("cannot combine --launch-tor and --tor-control-port=")
    timing = timing or DebugTiming()

    # Connect to an existing Tor, or create a new one. If we need to
    # launch an onion service, then we need a working control port (and
    # authentication cookie). If we're only acting as a client, we don't
    # need the control port.

    if launch_tor:
        print(
            " launching a new Tor process, this may take a while..",
            file=stderr)
        with timing.add("launch tor"):
            tor = yield txtorcon.launch(reactor,
                                        # data_directory=,
                                        # tor_binary=,
                                        )
    elif tor_control_port:
        with timing.add("find tor"):
            control_ep = clientFromString(reactor, tor_control_port)
            tor = yield txtorcon.connect(reactor, control_ep)  # might raise
            print(
                " using Tor via control port at %s" % tor_control_port,
                file=stderr)
    else:
        # Let txtorcon look through a list of usual places. If that fails,
        # we'll arrange to attempt the default SOCKS port
        with timing.add("find tor"):
            try:
                tor = yield txtorcon.connect(reactor)
                print(" using Tor via default control port", file=stderr)
            except Exception:
                # TODO: make this more specific. I think connect() is
                # likely to throw a reactor.connectTCP -type error, like
                # ConnectionFailed or ConnectionRefused or something
                print(
                    " unable to find default Tor control port, using SOCKS",
                    file=stderr)
                tor = SocksOnlyTor(reactor)
    directlyProvides(tor, _interfaces.ITorManager)
    returnValue(tor)
