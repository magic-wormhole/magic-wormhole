from collections import defaultdict
from binascii import hexlify
from attr import attrs, attrib
from attr.validators import instance_of, optional
from automat import MethodicalMachine
from zope.interface import implementer
from twisted.internet.task import deferLater
from twisted.internet.defer import DeferredList, CancelledError
from twisted.internet.endpoints import serverFromString
from twisted.internet.protocol import ClientFactory, ServerFactory
from twisted.internet.address import HostnameAddress, IPv4Address, IPv6Address
from twisted.internet.error import ConnectingCancelledError, ConnectionRefusedError, DNSLookupError
from twisted.python import log
from .. import ipaddrs  # TODO: move into _dilation/
from .._interfaces import IDilationConnector, IDilationManager
from ..timing import DebugTiming
from ..observer import EmptyableSet
from ..util import HKDF, to_unicode, provides
from .connection import DilatedConnectionProtocol, KCM
from .roles import LEADER

from .._hints import (DirectTCPV1Hint, TorTCPV1Hint, RelayV1Hint,
                      parse_hint_argv, describe_hint_obj, endpoint_from_hint_obj,
                      encode_hint)
from .._status import DilationHint
from ._noise import NoiseConnection


def build_sided_relay_handshake(key, side):
    assert isinstance(side, str)
    # magic-wormhole-transit-relay expects a specific layout for the
    # handshake message: "please relay {64} for side {16}\n"
    assert len(side) == 8 * 2, side
    token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
    return (b"please relay " + hexlify(token) +
            b" for side " + side.encode("ascii") + b"\n")


PROLOGUE_LEADER = b"Magic-Wormhole Dilation Handshake v1 Leader\n\n"
PROLOGUE_FOLLOWER = b"Magic-Wormhole Dilation Handshake v1 Follower\n\n"
NOISEPROTO = b"Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s"


def build_noise():
    return NoiseConnection.from_name(NOISEPROTO)


@attrs(eq=False)
@implementer(IDilationConnector)
class Connector:
    """I manage a single generation of connection.

    The Manager creates one of me at a time, whenever it wants a connection
    (which is always, once w.dilate() has been called and we know the remote
    end can dilate, and is expressed by the Manager calling my .start()
    method). I am discarded when my established connection is lost (and if we
    still want to be connected, a new generation is started and a new
    Connector is created). I am also discarded if we stop wanting to be
    connected (which the Manager expresses by calling my .stop() method).

    I manage the race between multiple connections for a specific generation
    of the dilated connection.

    I send connection hints when my InboundConnectionFactory yields addresses
    (self.listener_ready), and I initiate outbond connections (with
    OutboundConnectionFactory) as I receive connection hints from my peer
    (self.got_hints). Both factories use my build_protocol() method to create
    connection.DilatedConnectionProtocol instances. I track these protocol
    instances until one finishes negotiation and wins the race. I then shut
    down the others, remember the winner as self._winning_connection, and
    deliver the winner to manager.connector_connection_made(c).

    When an active connection is lost, we call manager.connector_connection_lost,
    allowing the manager to decide whether it wants to start a new generation
    or not.
    """

    _dilation_key = attrib(validator=instance_of(bytes))
    _transit_relay_location = attrib(validator=optional(instance_of(str)))
    _manager = attrib(validator=provides(IDilationManager))
    _reactor = attrib()
    _eventual_queue = attrib()
    _no_listen = attrib(validator=instance_of(bool))
    _tor = attrib()
    _timing = attrib()
    _side = attrib(validator=instance_of(str))
    # was self._side = bytes_to_hexstr(os.urandom(8)) # unicode
    _role = attrib()

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)  # pragma: no cover

    RELAY_DELAY = 2.0

    def __attrs_post_init__(self):
        if self._transit_relay_location:
            # TODO: allow multiple hints for a single relay
            relay_hint = parse_hint_argv(self._transit_relay_location)
            relay = RelayV1Hint(hints=(relay_hint,))
            self._transit_relays = [relay]
        else:
            self._transit_relays = []
        self._listeners = set()  # IListeningPorts that can be stopped
        self._pending_connectors = set()  # Deferreds that can be cancelled
        self._pending_connections = EmptyableSet(
            _eventual_queue=self._eventual_queue)  # Protocols to be stopped
        self._contenders = set()  # viable connections
        self._winning_connection = None
        self._timing = self._timing or DebugTiming()
        self._timing.add("transit")

    # this describes what our Connector can do, for the initial advertisement
    @classmethod
    def get_connection_abilities(klass):
        return [{"type": "direct-tcp-v1"},
                {"type": "relay-v1"},
                ]

    def build_protocol(self, addr, description):
        # encryption: let's use Noise NNpsk0 (or maybe NNpsk2). That uses
        # ephemeral keys plus a pre-shared symmetric key (the Transit key), a
        # different one for each potential connection.
        noise = build_noise()
        noise.set_psks(self._dilation_key)
        if self._role is LEADER:
            noise.set_as_initiator()
            outbound_prologue = PROLOGUE_LEADER
            inbound_prologue = PROLOGUE_FOLLOWER
        else:
            noise.set_as_responder()
            outbound_prologue = PROLOGUE_FOLLOWER
            inbound_prologue = PROLOGUE_LEADER
        p = DilatedConnectionProtocol(self._eventual_queue, self._role,
                                      description,
                                      self, noise,
                                      outbound_prologue, inbound_prologue)
        return p

    @m.state(initial=True)
    def connecting(self):
        pass  # pragma: no cover

    @m.state()
    def connected(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def stopped(self):
        pass  # pragma: no cover

    # TODO: unify the tense of these method-name verbs

    @m.input()
    def got_hints(self, hint_objs):
        pass

    @m.input()
    def stop(self):
        pass

    # called by ourselves, when _start_listener() is ready
    @m.input()
    def listener_ready(self, hint_objs):
        pass

    # called when DilatedConnectionProtocol submits itself, after KCM
    # received
    @m.input()
    def add_candidate(self, c):
        pass

    # called by ourselves, via consider()
    @m.input()
    def accept(self, c):
        pass

    @m.output()
    def use_hints(self, hint_objs):
        self._use_hints(hint_objs)

    @m.output()
    def publish_hints(self, hint_objs):
        self._publish_hints(hint_objs)

    def _publish_hints(self, hint_objs):
        self._manager.send_hints([encode_hint(h) for h in hint_objs])

    @m.output()
    def consider(self, c):
        self._contenders.add(c)
        if self._role is LEADER:
            # for now, just accept the first one. TODO: be clever.
            self._eventual_queue.eventually(self.accept, c)
        else:
            # the follower always uses the first contender, since that's the
            # only one the leader picked
            self._eventual_queue.eventually(self.accept, c)

    @m.output()
    def select_and_stop_remaining(self, c):
        self._winning_connection = c
        self._contenders.clear()  # we no longer care who else came close
        # remove this winner from the losers, so we don't shut it down
        self._pending_connections.discard(c)
        # shut down losing connections
        self.stop_listeners()  # TODO: maybe keep it open? NAT/p2p assist
        self.stop_pending_connectors()
        self.stop_pending_connections()

        c.select(self._manager)  # subsequent frames go directly to the manager
        # c.select also wires up when_disconnected() to fire
        # manager.connector_connection_lost(). TODO: rename this, since the
        # Connector is no longer the one calling it
        if self._role is LEADER:
            # TODO: this should live in Connection
            c.send_record(KCM())  # leader sends KCM now
        self._manager.connector_connection_made(c)  # manager sends frames to Connection

    @m.output()
    def stop_everything(self):
        self.stop_listeners()
        self.stop_pending_connectors()
        self.stop_pending_connections()
        self.break_cycles()

    def stop_listeners(self):
        d = DeferredList([sub.stopListening() for sub in self._listeners])
        self._listeners.clear()
        return d  # synchronization for tests

    def stop_pending_connectors(self):
        for d in self._pending_connectors:
            d.cancel()

    def stop_pending_connections(self):
        d = self._pending_connections.when_next_empty()
        [c.disconnect() for c in self._pending_connections]
        return d

    def break_cycles(self):
        # help GC by forgetting references to things that reference us
        self._listeners.clear()
        self._pending_connectors.clear()
        self._pending_connections.clear()
        self._winning_connection = None

    connecting.upon(listener_ready, enter=connecting, outputs=[publish_hints])
    connecting.upon(got_hints, enter=connecting, outputs=[use_hints])
    connecting.upon(add_candidate, enter=connecting, outputs=[consider])
    connecting.upon(accept, enter=connected, outputs=[
                    select_and_stop_remaining])
    connecting.upon(stop, enter=stopped, outputs=[stop_everything])

    # once connected, we ignore everything except stop
    connected.upon(listener_ready, enter=connected, outputs=[])
    connected.upon(got_hints, enter=connected, outputs=[])
    # TODO: tell them to disconnect? will they hang out forever? I *think*
    # they'll drop this once they get a KCM on the winning connection.
    connected.upon(add_candidate, enter=connected, outputs=[])
    connected.upon(accept, enter=connected, outputs=[])
    connected.upon(stop, enter=stopped, outputs=[stop_everything])

    # from Manager: start, got_hints, stop
    # maybe add_candidate, accept

    def start(self):
        if not self._no_listen and not self._tor:
            addresses = self._get_listener_addresses()
            self._start_listener(addresses)
        if self._transit_relays:
            self._publish_hints(self._transit_relays)
            self._use_hints(self._transit_relays)

    def _get_listener_addresses(self):
        addresses = ipaddrs.find_addresses()
        non_loopback_addresses = [a for a in addresses if a != "127.0.0.1"]
        if non_loopback_addresses:
            # some test hosts, including the appveyor VMs, *only* have
            # 127.0.0.1, and the tests will hang badly if we remove it.
            addresses = non_loopback_addresses
        return addresses

    def _start_listener(self, addresses):
        # TODO: listen on a fixed port, if possible, for NAT/p2p benefits, also
        # to make firewall configs easier
        # TODO: retain listening port between connection generations?
        ep = serverFromString(self._reactor, "tcp:0")
        f = InboundConnectionFactory(self)
        d = ep.listen(f)

        def _listening(lp):
            # lp is an IListeningPort
            self._listeners.add(lp)  # for shutdown and tests
            portnum = lp.getHost().port
            direct_hints = [DirectTCPV1Hint(to_unicode(addr), portnum, 0.0)
                            for addr in addresses]
            self.listener_ready(direct_hints)
        d.addCallback(_listening)
        d.addErrback(log.err)

    def _schedule_connection(self, delay, h, is_relay):
        ep = endpoint_from_hint_obj(h, self._tor, self._reactor)
        desc = describe_hint_obj(h, is_relay, self._tor)
        d = deferLater(self._reactor, delay,
                       self._connect, ep, desc, is_relay)
        d.addErrback(lambda f: f.trap(ConnectingCancelledError,
                                      ConnectionRefusedError,
                                      CancelledError,
                                      ))
        # TODO: HostnameEndpoint.connect catches CancelledError and replaces
        # it with DNSLookupError. Remove this workaround when
        # https://twistedmatrix.com/trac/ticket/9696 is fixed.
        d.addErrback(lambda f: f.trap(DNSLookupError))
        d.addErrback(log.err)
        self._pending_connectors.add(d)

    def _use_hints(self, hints):
        # first, pull out all the relays, we'll connect to them later
        relays = []
        direct = defaultdict(list)
        hint_status = []
        for h in hints:
            if isinstance(h, RelayV1Hint):
                relays.append(h)
            else:
                direct[h.priority].append(h)
        delay = 0.0
        made_direct = False
        priorities = sorted(set(direct.keys()), reverse=True)
        for p in priorities:
            for h in direct[p]:
                if isinstance(h, TorTCPV1Hint) and not self._tor:
                    continue
                hint_status.append(DilationHint(f"{h.hostname}:{h.port}", True))
                self._schedule_connection(delay, h, is_relay=False)
                made_direct = True
                # Make all direct connections immediately. Later, we'll change
                # the add_candidate() function to look at the priority when
                # deciding whether to accept a successful connection or not,
                # and it can wait for more options if it sees a higher-priority
                # one still running. But if we bail on that, we might consider
                # putting an inter-direct-hint delay here to influence the
                # process.
                # delay += 1.0

        if made_direct and not self._no_listen:
            # Prefer direct connections by stalling relay connections by a
            # few seconds. We don't wait until direct connections have
            # failed, because many direct hints will be to unused
            # local-network IP address, which won't answer, and can take the
            # full 30s TCP timeout to fail.
            #
            # If we didn't make any direct connections, or we're using
            # --no-listen, then we're probably going to have to use the
            # relay, so don't delay it at all.
            delay += self.RELAY_DELAY

        # It might be nice to wire this so that a failure in the direct hints
        # causes the relay hints to be used right away (fast failover). But
        # none of our current use cases would take advantage of that: if we
        # have any viable direct hints, then they're either going to succeed
        # quickly or hang for a long time.
        for r in relays:
            for h in r.hints:
                self._schedule_connection(delay, h, is_relay=True)
                hint_status.append(DilationHint(f"{h.hostname}:{h.port}", False))

        self._manager._hint_status(hint_status)
        # TODO:
        # if not contenders:
        #    raise TransitError("No contenders for connection")

    # TODO: add 2*TIMEOUT deadline for first generation, don't wait forever for
    # the initial connection

    def _connect(self, ep, description, is_relay=False):
        relay_handshake = None
        if is_relay:
            relay_handshake = build_sided_relay_handshake(self._dilation_key,
                                                          self._side)
        f = OutboundConnectionFactory(self, relay_handshake, description)
        d = ep.connect(f)
        # fires with protocol, or ConnectError

        def _connected(p):
            self._pending_connections.add(p)
            # c might not be in _pending_connections, if it turned out to be a
            # winner, which is why we use discard() and not remove()
            p.when_disconnected().addCallback(self._pending_connections.discard)
        d.addCallback(_connected)
        return d

    # Connection selection. All instances of DilatedConnectionProtocol which
    # look viable get passed into our add_contender() method.

    # On the Leader side, "viable" means we've seen their KCM frame, which is
    # the first Noise-encrypted packet on any given connection, and it has an
    # empty body. We gather viable connections until we see one that we like,
    # or a timer expires. Then we "select" it, close the others, and tell our
    # Manager to use it.

    # On the Follower side, we'll only see a KCM on the one connection selected
    # by the Leader, so the first viable connection wins.

    # our Connection protocols call: add_candidate


@attrs(repr=False)
class OutboundConnectionFactory(ClientFactory):
    _connector = attrib(validator=provides(IDilationConnector))
    _relay_handshake = attrib(validator=optional(instance_of(bytes)))
    _description = attrib()

    def __repr__(self):
        return f"OutboundConnectionFactory({self._connector._role} {self._description})"

    def buildProtocol(self, addr):
        p = self._connector.build_protocol(addr, self._description)
        p.factory = self
        if self._relay_handshake is not None:
            p.use_relay(self._relay_handshake)
        return p


def describe_inbound(addr):
    if isinstance(addr, HostnameAddress):
        return "<-tcp:%s:%d" % (addr.hostname, addr.port)
    elif isinstance(addr, IPv4Address):
        return "<-tcp:%s:%d" % (addr.host, addr.port)
    elif isinstance(addr, IPv6Address):
        return "<-tcp:[%s]:%d" % (addr.host, addr.port)
    return f"<-{addr!r}"


@attrs(repr=False)
class InboundConnectionFactory(ServerFactory):
    _connector = attrib(validator=provides(IDilationConnector))

    def __repr__(self):
        return f"InboundConnectionFactory({self._connector._role})"

    def buildProtocol(self, addr):
        description = describe_inbound(addr)
        p = self._connector.build_protocol(addr, description)
        p.factory = self
        return p
