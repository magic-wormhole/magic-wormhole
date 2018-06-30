from __future__ import print_function, unicode_literals
import sys
import re
from collections import defaultdict, namedtuple
from binascii import hexlify
import six
from attr import attrs, attrib
from attr.validators import instance_of, provides, optional
from automat import MethodicalMachine
from zope.interface import implementer
from twisted.internet.task import deferLater
from twisted.internet.defer import DeferredList
from twisted.internet.endpoints import HostnameEndpoint, serverFromString
from twisted.internet.protocol import ClientFactory, ServerFactory
from twisted.python import log
from hkdf import Hkdf
from .. import ipaddrs  # TODO: move into _dilation/
from .._interfaces import IDilationConnector, IDilationManager
from ..timing import DebugTiming
from ..observer import EmptyableSet
from .connection import DilatedConnectionProtocol, KCM
from .roles import LEADER


# These namedtuples are "hint objects". The JSON-serializable dictionaries
# are "hint dicts".

# DirectTCPV1Hint and TorTCPV1Hint mean the following protocol:
# * make a TCP connection (possibly via Tor)
# * send the sender/receiver handshake bytes first
# * expect to see the receiver/sender handshake bytes from the other side
# * the sender writes "go\n", the receiver waits for "go\n"
# * the rest of the connection contains transit data
DirectTCPV1Hint = namedtuple(
    "DirectTCPV1Hint", ["hostname", "port", "priority"])
TorTCPV1Hint = namedtuple("TorTCPV1Hint", ["hostname", "port", "priority"])
# RelayV1Hint contains a tuple of DirectTCPV1Hint and TorTCPV1Hint hints (we
# use a tuple rather than a list so they'll be hashable into a set). For each
# one, make the TCP connection, send the relay handshake, then complete the
# rest of the V1 protocol. Only one hint per relay is useful.
RelayV1Hint = namedtuple("RelayV1Hint", ["hints"])


def describe_hint_obj(hint, relay, tor):
    prefix = "tor->" if tor else "->"
    if relay:
        prefix = prefix + "relay:"
    if isinstance(hint, DirectTCPV1Hint):
        return prefix + "tcp:%s:%d" % (hint.hostname, hint.port)
    elif isinstance(hint, TorTCPV1Hint):
        return prefix + "tor:%s:%d" % (hint.hostname, hint.port)
    else:
        return prefix + str(hint)


def parse_hint_argv(hint, stderr=sys.stderr):
    assert isinstance(hint, type(""))
    # return tuple or None for an unparseable hint
    priority = 0.0
    mo = re.search(r'^([a-zA-Z0-9]+):(.*)$', hint)
    if not mo:
        print("unparseable hint '%s'" % (hint,), file=stderr)
        return None
    hint_type = mo.group(1)
    if hint_type != "tcp":
        print("unknown hint type '%s' in '%s'" % (hint_type, hint),
              file=stderr)
        return None
    hint_value = mo.group(2)
    pieces = hint_value.split(":")
    if len(pieces) < 2:
        print("unparseable TCP hint (need more colons) '%s'" % (hint,),
              file=stderr)
        return None
    mo = re.search(r'^(\d+)$', pieces[1])
    if not mo:
        print("non-numeric port in TCP hint '%s'" % (hint,), file=stderr)
        return None
    hint_host = pieces[0]
    hint_port = int(pieces[1])
    for more in pieces[2:]:
        if more.startswith("priority="):
            more_pieces = more.split("=")
            try:
                priority = float(more_pieces[1])
            except ValueError:
                print("non-float priority= in TCP hint '%s'" % (hint,),
                      file=stderr)
                return None
    return DirectTCPV1Hint(hint_host, hint_port, priority)


def parse_tcp_v1_hint(hint):  # hint_struct -> hint_obj
    hint_type = hint.get("type", "")
    if hint_type not in ["direct-tcp-v1", "tor-tcp-v1"]:
        log.msg("unknown hint type: %r" % (hint,))
        return None
    if not("hostname" in hint and
           isinstance(hint["hostname"], type(""))):
        log.msg("invalid hostname in hint: %r" % (hint,))
        return None
    if not("port" in hint and
           isinstance(hint["port"], six.integer_types)):
        log.msg("invalid port in hint: %r" % (hint,))
        return None
    priority = hint.get("priority", 0.0)
    if hint_type == "direct-tcp-v1":
        return DirectTCPV1Hint(hint["hostname"], hint["port"], priority)
    else:
        return TorTCPV1Hint(hint["hostname"], hint["port"], priority)


def parse_hint(hint_struct):
    hint_type = hint_struct.get("type", "")
    if hint_type == "relay-v1":
        # the struct can include multiple ways to reach the same relay
        rhints = filter(lambda h: h,  # drop None (unrecognized)
                        [parse_tcp_v1_hint(rh) for rh in hint_struct["hints"]])
        return RelayV1Hint(rhints)
    return parse_tcp_v1_hint(hint_struct)


def encode_hint(h):
    if isinstance(h, DirectTCPV1Hint):
        return {"type": "direct-tcp-v1",
                "priority": h.priority,
                "hostname": h.hostname,
                "port": h.port,  # integer
                }
    elif isinstance(h, RelayV1Hint):
        rhint = {"type": "relay-v1", "hints": []}
        for rh in h.hints:
            rhint["hints"].append({"type": "direct-tcp-v1",
                                   "priority": rh.priority,
                                   "hostname": rh.hostname,
                                   "port": rh.port})
        return rhint
    elif isinstance(h, TorTCPV1Hint):
        return {"type": "tor-tcp-v1",
                "priority": h.priority,
                "hostname": h.hostname,
                "port": h.port,  # integer
                }
    raise ValueError("unknown hint type", h)


def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)


def build_sided_relay_handshake(key, side):
    assert isinstance(side, type(u""))
    assert len(side) == 8 * 2
    token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
    return (b"please relay " + hexlify(token) +
            b" for side " + side.encode("ascii") + b"\n")


PROLOGUE_LEADER = b"Magic-Wormhole Dilation Handshake v1 Leader\n\n"
PROLOGUE_FOLLOWER = b"Magic-Wormhole Dilation Handshake v1 Follower\n\n"
NOISEPROTO = "Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s"


@attrs
@implementer(IDilationConnector)
class Connector(object):
    _dilation_key = attrib(validator=instance_of(type(b"")))
    _transit_relay_location = attrib(validator=optional(instance_of(str)))
    _manager = attrib(validator=provides(IDilationManager))
    _reactor = attrib()
    _eventual_queue = attrib()
    _no_listen = attrib(validator=instance_of(bool))
    _tor = attrib()
    _timing = attrib()
    _side = attrib(validator=instance_of(type(u"")))
    # was self._side = bytes_to_hexstr(os.urandom(8)) # unicode
    _role = attrib()

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)

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

    def build_protocol(self, addr):
        # encryption: let's use Noise NNpsk0 (or maybe NNpsk2). That uses
        # ephemeral keys plus a pre-shared symmetric key (the Transit key), a
        # different one for each potential connection.
        from noise.connection import NoiseConnection
        noise = NoiseConnection.from_name(NOISEPROTO)
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
    def listener_ready(self, hint_objs):
        pass

    @m.input()
    def add_relay(self, hint_objs):
        pass

    @m.input()
    def got_hints(self, hint_objs):
        pass

    @m.input()
    def add_candidate(self, c):  # called by DilatedConnectionProtocol
        pass

    @m.input()
    def accept(self, c):
        pass

    @m.input()
    def stop(self):
        pass

    @m.output()
    def use_hints(self, hint_objs):
        self._use_hints(hint_objs)

    @m.output()
    def publish_hints(self, hint_objs):
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
        if self._role is LEADER:
            # TODO: this should live in Connection
            c.send_record(KCM())  # leader sends KCM now
        self._manager.use_connection(c)  # manager sends frames to Connection

    @m.output()
    def stop_everything(self):
        self.stop_listeners()
        self.stop_pending_connectors()
        self.stop_pending_connections()
        self.break_cycles()

    def stop_listeners(self):
        d = DeferredList([l.stopListening() for l in self._listeners])
        self._listeners.clear()
        return d  # synchronization for tests

    def stop_pending_connectors(self):
        return DeferredList([d.cancel() for d in self._pending_connectors])

    def stop_pending_connections(self):
        d = self._pending_connections.when_next_empty()
        [c.loseConnection() for c in self._pending_connections]
        return d

    def stop_winner(self):
        d = self._winner.when_disconnected()
        self._winner.disconnect()
        return d

    def break_cycles(self):
        # help GC by forgetting references to things that reference us
        self._listeners.clear()
        self._pending_connectors.clear()
        self._pending_connections.clear()
        self._winner = None

    connecting.upon(listener_ready, enter=connecting, outputs=[publish_hints])
    connecting.upon(add_relay, enter=connecting, outputs=[use_hints,
                                                          publish_hints])
    connecting.upon(got_hints, enter=connecting, outputs=[use_hints])
    connecting.upon(add_candidate, enter=connecting, outputs=[consider])
    connecting.upon(accept, enter=connected, outputs=[
                    select_and_stop_remaining])
    connecting.upon(stop, enter=stopped, outputs=[stop_everything])

    # once connected, we ignore everything except stop
    connected.upon(listener_ready, enter=connected, outputs=[])
    connected.upon(add_relay, enter=connected, outputs=[])
    connected.upon(got_hints, enter=connected, outputs=[])
    connected.upon(add_candidate, enter=connected, outputs=[])
    connected.upon(accept, enter=connected, outputs=[])
    connected.upon(stop, enter=stopped, outputs=[stop_everything])

    # from Manager: start, got_hints, stop
    # maybe add_candidate, accept

    def start(self):
        self._start_listener()
        if self._transit_relays:
            self.publish_hints(self._transit_relays)
            self._use_hints(self._transit_relays)

    def _start_listener(self):
        if self._no_listen or self._tor:
            return
        addresses = ipaddrs.find_addresses()
        non_loopback_addresses = [a for a in addresses if a != "127.0.0.1"]
        if non_loopback_addresses:
            # some test hosts, including the appveyor VMs, *only* have
            # 127.0.0.1, and the tests will hang badly if we remove it.
            addresses = non_loopback_addresses
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
            direct_hints = [DirectTCPV1Hint(six.u(addr), portnum, 0.0)
                            for addr in addresses]
            self.listener_ready(direct_hints)
        d.addCallback(_listening)
        d.addErrback(log.err)

    def _use_hints(self, hints):
        # first, pull out all the relays, we'll connect to them later
        relays = defaultdict(list)
        direct = defaultdict(list)
        for h in hints:
            if isinstance(h, RelayV1Hint):
                relays[h.priority].append(h)
            else:
                direct[h.priority].append(h)
        delay = 0.0
        priorities = sorted(set(direct.keys()), reverse=True)
        for p in priorities:
            for h in direct[p]:
                if isinstance(h, TorTCPV1Hint) and not self._tor:
                    continue
                ep = self._endpoint_from_hint_obj(h)
                desc = describe_hint_obj(h, False, self._tor)
                d = deferLater(self._reactor, delay,
                               self._connect, ep, desc, is_relay=False)
                self._pending_connectors.add(d)
                # Make all direct connections immediately. Later, we'll change
                # the add_candidate() function to look at the priority when
                # deciding whether to accept a successful connection or not,
                # and it can wait for more options if it sees a higher-priority
                # one still running. But if we bail on that, we might consider
                # putting an inter-direct-hint delay here to influence the
                # process.
                # delay += 1.0
        if delay > 0.0:
            # Start trying the relays a few seconds after we start to try the
            # direct hints. The idea is to prefer direct connections, but not
            # be afraid of using a relay when we have direct hints that don't
            # resolve quickly. Many direct hints will be to unused
            # local-network IP addresses, which won't answer, and would take
            # the full TCP timeout (30s or more) to fail. If there were no
            # direct hints, don't delay at all.
            delay += self.RELAY_DELAY

        # prefer direct connections by stalling relay connections by a few
        # seconds, unless we're using --no-listen in which case we're probably
        # going to have to use the relay
        delay = self.RELAY_DELAY if self._no_listen else 0.0

        # It might be nice to wire this so that a failure in the direct hints
        # causes the relay hints to be used right away (fast failover). But
        # none of our current use cases would take advantage of that: if we
        # have any viable direct hints, then they're either going to succeed
        # quickly or hang for a long time.
        for p in priorities:
            for r in relays[p]:
                for h in r.hints:
                    ep = self._endpoint_from_hint_obj(h)
                    desc = describe_hint_obj(h, True, self._tor)
                    d = deferLater(self._reactor, delay,
                                   self._connect, ep, desc, is_relay=True)
                    self._pending_connectors.add(d)
        # TODO:
        # if not contenders:
        #    raise TransitError("No contenders for connection")

    # TODO: add 2*TIMEOUT deadline for first generation, don't wait forever for
    # the initial connection

    def _connect(self, h, ep, description, is_relay=False):
        relay_handshake = None
        if is_relay:
            relay_handshake = build_sided_relay_handshake(self._dilation_key,
                                                          self._side)
        f = OutboundConnectionFactory(self, relay_handshake)
        d = ep.connect(f)
        # fires with protocol, or ConnectError

        def _connected(p):
            self._pending_connections.add(p)
            # c might not be in _pending_connections, if it turned out to be a
            # winner, which is why we use discard() and not remove()
            p.when_disconnected().addCallback(self._pending_connections.discard)
        d.addCallback(_connected)
        return d

    def _endpoint_from_hint_obj(self, hint):
        if self._tor:
            if isinstance(hint, (DirectTCPV1Hint, TorTCPV1Hint)):
                # this Tor object will throw ValueError for non-public IPv4
                # addresses and any IPv6 address
                try:
                    return self._tor.stream_via(hint.hostname, hint.port)
                except ValueError:
                    return None
            return None
        if isinstance(hint, DirectTCPV1Hint):
            return HostnameEndpoint(self._reactor, hint.hostname, hint.port)
        return None

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


@attrs
class OutboundConnectionFactory(ClientFactory, object):
    _connector = attrib(validator=provides(IDilationConnector))
    _relay_handshake = attrib(validator=optional(instance_of(bytes)))

    def buildProtocol(self, addr):
        p = self._connector.build_protocol(addr)
        p.factory = self
        if self._relay_handshake is not None:
            p.use_relay(self._relay_handshake)
        return p


@attrs
class InboundConnectionFactory(ServerFactory, object):
    _connector = attrib(validator=provides(IDilationConnector))
    protocol = DilatedConnectionProtocol

    def buildProtocol(self, addr):
        p = self._connector.build_protocol(addr)
        p.factory = self
        return p
