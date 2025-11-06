import os
from collections import deque
from attr import attrs, attrib, evolve, define, field
from attr.validators import instance_of, optional
from automat import MethodicalMachine
from zope.interface import implementer
from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.python import log, failure
from .._interfaces import IDilator, IDilationManager, ISend, ITerminator
from ..util import dict_to_bytes, bytes_to_dict, bytes_to_hexstr, provides
from ..observer import OneShotObserver
from .._key import derive_key
from .subchannel import (_WormholeAddress,
                         SubchannelConnectorEndpoint,
                         SubchannelDemultiplex,
                         SubchannelListenerEndpoint)
from .connector import Connector
from .._hints import parse_hint
from .roles import LEADER, FOLLOWER
from .connection import KCM, Ping, Pong, Open, Data, Close, Ack
from .inbound import Inbound
from .outbound import Outbound
from .._status import (DilationStatus, WormholeStatus,
                       ConnectedPeer, ConnectingPeer, ReconnectingPeer, StoppedPeer,
                       )


# exported to Wormhole() for inclusion in versions message
# note that these are strings, not numbers, to facilitate
# experimentation or non-standard versions; the _order_ of versions in
# "can-dilate" is important!
# versions shall be named after wizards from the "Earthsea" series by le Guin
DILATION_VERSIONS = ["ged"]


class OldPeerCannotDilateError(Exception):
    pass


class UnknownDilationMessageType(Exception):
    pass


class ReceivedHintsTooEarly(Exception):
    pass


class UnexpectedKCM(Exception):
    pass


class UnknownMessageType(Exception):
    pass


@define
class DilatedWormhole:
    """
    Represents actions available once a wormhole has been successfully dilated.

    New subchannels to the other peer may be established by first
    obtaining an `IStreamClientEndpoint` from the
    `subprotocol_connector_for("subproto-name")` method. Note that
    ``.connect()`` on these endpoints will ``.errback()`` if Dilation
    cannot be established.
    """

    _manager: IDilationManager = field()

    @inlineCallbacks
    def when_dilated(self):
        yield self._manager._main_channel.when_fired()
        return None

    def listener_for(self, subprotocol_name):
        """
        :returns: an IStreamServerEndpoint that may be used to listen for
           the creation of new subchannels with a particular name.

        Once ``.listen()`` is called on the returned endpoint, every
        new subchannel with this name will have ``.buildProtocol()``
        called, that is what you'd expect Twisted to do.

        (Can we errback something here if we entirely failed to dilate?)
        --> probably only if we make this API async?
        """
        return SubchannelListenerEndpoint(
            subprotocol_name,
            self._manager,
        )

    def connector_for(self, subprotocol_name):
        """
        :returns: an IStreamClientEndpoint that may be used to create new
            subchannels using a specific kind of subprotocol

        Once ``.connect()`` is called on the returned endpoint, a new
        subchannel is opened from this peer to the other peer. The
        other peer sees an OPEN and instantiates a listener from the
        Factory it was given during creation of the wormhole.
        """
        return SubchannelConnectorEndpoint(
            subprotocol_name,
            self._manager,
            self._manager._host_addr,
            self._manager._eventual_queue,
        )


@attrs
class Once:
    _errtype = attrib()

    def __attrs_post_init__(self):
        self._called = False

    def __call__(self):
        if self._called:
            raise self._errtype()
        self._called = True


class CanOnlyDilateOnceError(Exception):
    pass


def make_side():
    return bytes_to_hexstr(os.urandom(8))


# new scheme:
# * both sides send PLEASE as soon as they have an unverified key and
#    w.dilate has been called,
# * PLEASE includes a dilation-specific "side" (independent of the "side"
#    used by mailbox messages)
# * higher "side" is Leader, lower is Follower
# * PLEASE includes the selection of a version from the "can-dilate" list of versions, requires overlap
#    "1" is current

# * we start dilation after both w.dilate() and receiving VERSION, putting us
#   in WANTING, then we process all previously-queued inbound DILATE-n
#   messages. When PLEASE arrives, we move to CONNECTING
# * HINTS sent after dilation starts
# * only Leader sends RECONNECT, only Follower sends RECONNECTING. This
#    is the only difference between the two sides, and is not enforced
#    by the protocol (i.e. if the Follower sends RECONNECT to the Leader,
#    the Leader will obey, although TODO how confusing will this get?)
# * upon receiving RECONNECT: drop Connector, start new Connector, send
#   RECONNECTING, start sending HINTS
# * upon sending RECONNECT: go into FLUSHING state and ignore all HINTS until
#   RECONNECTING received. The new Connector can be spun up earlier, and it
#   can send HINTS, but it must not be given any HINTS that arrive before
#   RECONNECTING (since they're probably stale)

# * after VERSIONS(KCM) received, we might learn that the other side cannot
#    dilate. w.dilate errbacks at this point

# * maybe signal warning if we stay in a "want" state for too long
# * nobody sends HINTS until they're ready to receive
# * nobody sends HINTS unless they've called w.dilate() and received PLEASE
# * nobody connects to inbound hints unless they've called w.dilate()
# * if leader calls w.dilate() but not follower, leader waits forever in
#   "want" (doesn't send anything)
# * if follower calls w.dilate() but not leader, follower waits forever
#   in "want", leader waits forever in "wanted"


def _find_shared_versions(my_versions, their_versions): # -> Option[list]:
    """
    Decide on a best version given a ranked list of our and their
    versions (consisting of arbitrary strings). We prefer a higher
    version from 'our' list over the other list.
    """
    their_dilation_versions = set(their_versions)
    shared_versions = set(my_versions).intersection(their_dilation_versions)
    best_version = None

    if shared_versions:
        # the "best" one is whichever version is highest up the
        # list of acceptable versions
        best = sorted([
            (my_versions.index(v), v)
            for v in shared_versions
        ])
        best_version = best[0][1]

    # dilation_version is the best mutually-compatible version we have
    # with the peer, or None if we have nothing in common
    return best_version


@attrs(eq=False)
class TrafficTimer:
    """
    Tracks when timers have expired versus when traffic (usually
    Pongs) has been seen.

    Will trigger a re-connect (if two timer-intervals expire before we
    see traffic).

    The actual timer (and its length) is controlled by the Manager, as
    is the re-connection logic.
    """

    on_reconnect = attrib()  # a callback when a re-connection attempt is required
    start_timer = attrib()  # a callable that should start the interval timer

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)  # pragma: no cover

    @m.state(initial=True)
    def no_connection(self):
        """
        We aren't even connected yet
        """

    @m.state()
    def connected(self):
        """
        We are connected, and have recently seen traffic
        """

    @m.state()
    def idle_traffic(self):
        """
        We haven't seen any data for an interval
        """

    @m.input()
    def interval_elapsed(self):
        """
        One interval is an arbitrary amount of time; when two have
        elapsed, we emit a 'reconnect' signal
        """

    @m.input()
    def traffic_seen(self):
        """
        We have seen some traffic
        """

    @m.input()
    def got_connection(self):
        """
        A connection has been established
        """

    @m.input()
    def lost_connection(self):
        """
        The connection has been lost
        """

    @m.output()
    def signal_reconnect(self):
        self.on_reconnect()

    @m.output()
    def begin_timing(self):
        self.start_timer()

    no_connection.upon(
        got_connection,
        enter=connected,
        outputs=[begin_timing]
    )
    no_connection.upon(
        interval_elapsed,
        enter=no_connection,
        outputs=[begin_timing]
    )
    connected.upon(
        lost_connection,
        enter=no_connection,
        outputs=[]
    )

    connected.upon(
        interval_elapsed,
        enter=idle_traffic,
        outputs=[begin_timing]
    )
    connected.upon(
        traffic_seen,
        enter=connected,
        outputs=[begin_timing]
    )

    idle_traffic.upon(
        interval_elapsed,
        enter=connected,
        outputs=[signal_reconnect]
    )
    idle_traffic.upon(
        traffic_seen,
        enter=connected,
        outputs=[]
    )
    idle_traffic.upon(
        lost_connection,
        enter=no_connection,
        outputs=[]
    )


@attrs(eq=False)
@implementer(IDilationManager)
class Manager:
    _S = attrib(validator=provides(ISend), repr=False)
    _my_side = attrib(validator=instance_of(str))
    _transit_relay_location = attrib(validator=optional(instance_of(str)))
    _reactor = attrib(repr=False)
    _eventual_queue = attrib(repr=False)
    _cooperator = attrib(repr=False)
    _acceptable_versions = attrib()
    _ping_interval = attrib(validator=instance_of(float))
    _expected_subprotocols = attrib()
    # TODO: can this validator work when the parameter is optional?
    _no_listen = attrib(validator=instance_of(bool), default=False)
    _status = attrib(default=None)  # callable([DilationStatus])
    _initial_mailbox_status = attrib(default=None)  # WormholeStatus

    _dilation_key = None
    _tor = None  # TODO
    _timing = None  # TODO
    _next_subchannel_id = None  # initialized in choose_role
    _dilation_version = None  # initialized in got_wormhole_versions
    _main_channel = None  # initialized in __attrs_port_init__
    _subprotocol_factories = None  # initialized in __attrs_port_init__

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._got_versions_d = Deferred()

        self._my_role = None  # determined upon rx_PLEASE
        self._host_addr = _WormholeAddress()

        self._connection = None
        self._made_first_connection = False
        self._stopped = OneShotObserver(self._eventual_queue)
        self._debug_stall_connector = False

        self._next_dilation_generation = 0
        self._latest_status = DilationStatus(mailbox=self._initial_mailbox_status or WormholeStatus(), generation=0)
        # do not "del" this, the attrs __repr__ gets sad
        self._initial_mailbox_status = None

        # I kept getting confused about which methods were for inbound data
        # (and thus flow-control methods go "out") and which were for
        # outbound data (with flow-control going "in"), so I split them up
        # into separate pieces.
        self._inbound = Inbound(self, self._host_addr)
        self._outbound = Outbound(self, self._cooperator)  # from us to peer

        # TODO: let inbound/outbound create the endpoints, then return them
        # to us
        self._main_channel = OneShotObserver(self._eventual_queue)
        self._subprotocol_factories = SubchannelDemultiplex()

        # NOTE: circular refs, not ideal
        self._api = DilatedWormhole(self)

        # maps outstanding ping_id's (4 bytes) to a 2-tuple (callback, timestamp)
        # (the callback is provided when send_ping is called)
        self._pings_outstanding = dict()

        # Manage our notion of "we have seen traffic recently" or not
        # Only the Leader does this (as only it can decide "we need a
        # new generation")
        self._traffic = None
        self._timer = None

    def _signal_reconnect(self):
        """
        Called by the TrafficTimer machine if we should re-connect (due to
        missed pings)
        """
        if self._connection:
            self._connection.disconnect()

    def _send_ping_reset_timer(self):
        """
        Called by the TrafficTimer machine whenever we should start (or
        extend) our timer and send a ping
        """

        def got_pong(_):
            # ignoring "ping_id"
            self._traffic.traffic_seen()

        self.send_ping(os.urandom(4), got_pong)
        if self._timer is None:
            def timer_expired():
                self._timer = None
                self._traffic.interval_elapsed()
            self._timer = self._reactor.callLater(self._ping_interval, timer_expired)
        else:
            # we already have a timer runner, so extend it
            self._timer.delay(self._ping_interval)

    def _register_subprotocol_factory(self, name, factory):
        """
        Internal helper. Application code has asked to listen for a
        particular subprotocol.  It is an error to listen twice on the
        same subprotocol.
        """
        self._subprotocol_factories.register(name, factory)

    def got_dilation_key(self, key):
        assert isinstance(key, bytes)
        self._dilation_key = key

    def got_wormhole_versions(self, their_wormhole_versions):
        # this always happens before received_dilation_message
        self._dilation_version = _find_shared_versions(
            self._acceptable_versions,
            their_wormhole_versions.get("can-dilate", [])
        )

        if not self._dilation_version:  # "ged" or None
            # TODO: be more specific about the error. dilation_version==None
            # means we had no version in common with them, which could either
            # be because they're so old they don't dilate at all, or because
            # they're so new that they no longer accommodate our old version
            self.fail(failure.Failure(OldPeerCannotDilateError()))

        self.start()

    # from _boss.Boss
    def _wormhole_status(self, wormhole_status):
        self._maybe_send_status(
            evolve(
                self._latest_status,
                mailbox=wormhole_status,
            )
        )

    def _maybe_send_status(self, status_msg):
        self._latest_status = status_msg
        if self._status is not None:
            self._status(status_msg)

    def _hint_status(self, hints):
        """
        Internal helper. From Connector, calls to update the hints we're
        actually using
        """
        self._maybe_send_status(
            evolve(
                self._latest_status,
                hints=set(hints).union(self._latest_status.hints),
            )
        )

    def fail(self, f):
        self._main_channel.error(f)

    def received_dilation_message(self, plaintext):
        # this receives new in-order DILATE-n payloads, decrypted but not
        # de-JSONed.

        message = bytes_to_dict(plaintext)
        type = message["type"]
        if type == "please":
            self.rx_PLEASE(message)
        elif type == "connection-hints":
            self.rx_HINTS(message)
            # todo: could be useful to put "hints" in status, and send
            # a status update when getting new hints?
        elif type == "reconnect":
            self.rx_RECONNECT()
        elif type == "reconnecting":
            self.rx_RECONNECTING()
        else:
            log.err(UnknownDilationMessageType(message))
            return

    def when_stopped(self):
        return self._stopped.when_fired()

    def send_dilation_generation(self, **fields):
        dilation_generation = self._next_dilation_generation
        self._next_dilation_generation += 1
        self._S.send("dilate-%d" % dilation_generation, dict_to_bytes(fields))

    def send_hints(self, hints):  # from Connector
        self.send_dilation_generation(type="connection-hints", hints=hints)

    # forward inbound-ish things to _Inbound

    def subchannel_pauseProducing(self, sc):
        self._inbound.subchannel_pauseProducing(sc)

    def subchannel_resumeProducing(self, sc):
        self._inbound.subchannel_resumeProducing(sc)

    def subchannel_stopProducing(self, sc):
        self._inbound.subchannel_stopProducing(sc)

    def subchannel_local_open(self, scid, sc):
        self._inbound.subchannel_local_open(scid, sc)

    # forward outbound-ish things to _Outbound
    def subchannel_registerProducer(self, sc, producer, streaming):
        self._outbound.subchannel_registerProducer(sc, producer, streaming)

    def subchannel_unregisterProducer(self, sc):
        self._outbound.subchannel_unregisterProducer(sc)

    def send_open(self, scid, subprotocol):
        assert isinstance(scid, int)
        self._queue_and_send(Open, scid, subprotocol)

    def send_data(self, scid, data):
        assert isinstance(scid, int)
        self._queue_and_send(Data, scid, data)

    def send_close(self, scid):
        assert isinstance(scid, int)
        self._queue_and_send(Close, scid)

    def _queue_and_send(self, record_type, *args):
        r = self._outbound.build_record(record_type, *args)
        # Outbound owns the send_record() pipe, so that it can stall new
        # writes after a new connection is made until after all queued
        # messages are written (to preserve ordering).
        self._outbound.queue_and_send_record(r)  # may trigger pauseProducing

    def subchannel_closed(self, scid, sc):
        # let everyone clean up. This happens just after we delivered
        # connectionLost to the Protocol, except for the control channel,
        # which might get connectionLost later after they use ep.connect.
        # TODO: is this inversion a problem?
        self._inbound.subchannel_closed(scid, sc)
        self._outbound.subchannel_closed(scid, sc)

    # our Connector calls these

    def connector_connection_made(self, c):
        # only the "Leader" will do pings, because it is the only side
        # which can decide "our peer connection is dead"
        if self._my_role == LEADER:
            # if we have just RE-connected, then we'll already have a
            # _traffic instance but the first time we connect we do not
            if self._traffic is None:
                self._traffic = TrafficTimer(self._signal_reconnect, self._send_ping_reset_timer)
            self._traffic.got_connection()

        self.connection_made()  # state machine update
        self._connection = c
        self._inbound.use_connection(c)
        self._outbound.use_connection(c)  # does c.registerProducer
        if not self._made_first_connection:
            self._made_first_connection = True
            # might be ideal to send information about our selected
            # Peer connection through here
            self._main_channel.fire(None)
        pass

    def connector_connection_lost(self):
        # ultimately called after a DilatedConnectionProtocol disconnects
        if self._traffic is not None:
            self._traffic.lost_connection()
        self._stop_using_connection()
        if self._my_role is LEADER:
            self.connection_lost_leader()  # state machine
        else:
            self.connection_lost_follower()

    def _stop_using_connection(self):
        # the connection is already lost by this point
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._connection = None
        self._inbound.stop_using_connection()
        self._outbound.stop_using_connection()  # does c.unregisterProducer

    # from our active Connection

    def got_record(self, r):
        # records with sequence numbers: always ack, ignore old ones
        if isinstance(r, (Open, Data, Close)):
            self.send_ack(r.seqnum)  # always ack, even for old ones
            if self._inbound.is_record_old(r):
                return
            self._inbound.update_ack_watermark(r.seqnum)
            if isinstance(r, Open):
                self._inbound.handle_open(r.scid, r.subprotocol)
            elif isinstance(r, Data):
                self._inbound.handle_data(r.scid, r.data)
            else:  # isinstance(r, Close)
                self._inbound.handle_close(r.scid)
            return
        if isinstance(r, KCM):
            log.err(UnexpectedKCM())
        elif isinstance(r, Ping):
            self.handle_ping(r.ping_id)
        elif isinstance(r, Pong):
            self.handle_pong(r.ping_id)
        elif isinstance(r, Ack):
            self._outbound.handle_ack(r.resp_seqnum)  # retire queued messages
        else:
            log.err(UnknownMessageType(f"{r}"))
        # todo: it might be better to tell the TrafficTimer
        # state-machine every time we see _any_ traffic (i.e. here)
        # -- currently we're demanding that we see the "Pong"
        # if self._traffic is not None:
        #     self._traffic.traffic_seen()

    # pings, pongs, and acks are not queued
    def send_ping(self, ping_id, on_pong=None):
        # ping_id is 4 bytes
        assert ping_id not in self._pings_outstanding, "Duplicate ping_id"
        self._pings_outstanding[ping_id] = (on_pong, self._reactor.seconds())
        self._outbound.send_if_connected(Ping(ping_id))

    def send_pong(self, ping_id):  # ping_id is bytes?
        self._outbound.send_if_connected(Pong(ping_id))

    def send_ack(self, resp_seqnum):
        self._outbound.send_if_connected(Ack(resp_seqnum))

    def handle_ping(self, ping_id):
        self._peer_saw_ping()
        self.send_pong(ping_id)

    def handle_pong(self, ping_id):
        if ping_id not in self._pings_outstanding:
            print("Weird: pong for ping that isn't outstanding")
        else:
            self._peer_saw_ping()
            on_pong, start = self._pings_outstanding.pop(ping_id)
            if on_pong is not None:
                on_pong(self._reactor.seconds() - start)
        # TODO: update is-alive timer

    # status

    def have_peer(self, conn):
        """
        Signal that we have selected a peer connection to use
        """
        self._maybe_send_status(
            evolve(
                self._latest_status,
                peer_connection=ConnectedPeer(
                    self._reactor.seconds(),
                    self._reactor.seconds() + (self._ping_interval * 2),
                    conn._description,
                ),
            )
        )

    def _peer_saw_ping(self):
        """
        We have just seen Ping or Pong traffic from our peer.

        Note that only the Leader sends Pings so one side will see
        only Pongs and one will see only Pings.
        """
        if isinstance(self._latest_status.peer_connection, ConnectedPeer):
            self._maybe_send_status(
                evolve(
                    self._latest_status,
                    peer_connection=evolve(
                        self._latest_status.peer_connection,
                        expires_at=self._reactor.seconds() + (self._ping_interval * 2),
                    )
                )
            )

    # subchannel maintenance
    def allocate_subchannel_id(self):
        scid_num = self._next_subchannel_id
        self._next_subchannel_id += 2
        return scid_num

    # state machine

    @m.state(initial=True)
    def WAITING(self):
        pass  # pragma: no cover

    @m.state()
    def WANTING(self):
        pass  # pragma: no cover

    @m.state()
    def CONNECTING(self):
        pass  # pragma: no cover

    @m.state()
    def CONNECTED(self):
        pass  # pragma: no cover

    @m.state()
    def FLUSHING(self):
        pass  # pragma: no cover

    @m.state()
    def ABANDONING(self):
        pass  # pragma: no cover

    @m.state()
    def LONELY(self):
        pass  # pragma: no cover

    @m.state()
    def STOPPING(self):
        pass  # pragma: no cover

    @m.state(terminal=True)
    def STOPPED(self):
        pass  # pragma: no cover

    @m.input()
    def start(self):
        pass  # pragma: no cover

    @m.input()
    def rx_PLEASE(self, message):
        pass  # pragma: no cover

    @m.input()  # only sent by Follower
    def rx_HINTS(self, hint_message):
        pass  # pragma: no cover

    @m.input()  # only Leader sends RECONNECT, so only Follower receives it
    def rx_RECONNECT(self):
        pass  # pragma: no cover

    @m.input()  # only Follower sends RECONNECTING, so only Leader receives it
    def rx_RECONNECTING(self):
        pass  # pragma: no cover

    # Connector gives us connection_made()
    @m.input()
    def connection_made(self):
        pass  # pragma: no cover

    # our connection_lost() fires connection_lost_leader or
    # connection_lost_follower depending upon our role. If either side sees a
    # problem with the connection (timeouts, bad authentication) then they
    # just drop it and let connection_lost() handle the cleanup.
    @m.input()
    def connection_lost_leader(self):
        pass  # pragma: no cover

    @m.input()
    def connection_lost_follower(self):
        pass

    @m.input()
    def stop(self):
        pass  # pragma: no cover

    @m.output()
    def send_please(self):
        msg = {
            "type": "please",
            "side": self._my_side,
        }
        if self._dilation_version is not None:
            msg["use-version"] = self._dilation_version
        self.send_dilation_generation(**msg)

    @m.output()
    def choose_role(self, message):
        their_side = message["side"]
        if self._my_side > their_side:
            self._my_role = LEADER
            # scid 0 is reserved for the control channel. the leader uses odd
            # numbers starting with 1
            self._next_subchannel_id = 1
        elif their_side > self._my_side:
            self._my_role = FOLLOWER
            # the follower uses even numbers starting with 2
            self._next_subchannel_id = 2
        else:
            raise ValueError("their side shouldn't be equal: reflection?")

    # these Outputs behave differently for the Leader vs the Follower

    @m.output()
    def start_connecting_ignore_message(self, message):
        del message  # ignored
        return self._start_connecting()

    @m.output()
    def start_connecting(self):
        self._start_connecting()

    def _start_connecting(self):
        assert self._my_role is not None
        assert self._dilation_key is not None
        self._connector = Connector(self._dilation_key,
                                    self._transit_relay_location,
                                    self,
                                    self._reactor, self._eventual_queue,
                                    self._no_listen, self._tor,
                                    self._timing,
                                    self._my_side,  # needed for relay handshake
                                    self._my_role)
        if self._debug_stall_connector:
            # unit tests use this hook to send messages while we know we
            # don't have a connection
            self._eventual_queue.eventually(self._debug_stall_connector, self._connector)
            return
        self._connector.start()

    @m.output()
    def send_reconnect(self):
        self.send_dilation_generation(type="reconnect")  # TODO: generation number?

    @m.output()
    def send_reconnecting(self):
        self.send_dilation_generation(type="reconnecting")  # TODO: generation?

    @m.output()
    def use_hints(self, hint_message):
        hint_objs = filter(lambda h: h,  # ignore None, unrecognizable
                           [parse_hint(hs) for hs in hint_message["hints"]])
        hint_objs = list(hint_objs)
        self._connector.got_hints(hint_objs)

    @m.output()
    def stop_connecting(self):
        self._connector.stop()

    @m.output()
    def abandon_connection(self):
        # we think we're still connected, but the Leader disagrees. Or we've
        # been told to shut down.
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._connection.disconnect()  # let connection_lost do cleanup

    @m.output()
    def notify_stopped(self):
        self._stopped.fire(None)

    @m.output()
    def send_status_connecting(self):
        self._maybe_send_status(
            evolve(
                self._latest_status,
                peer_connection=ConnectingPeer(self._reactor.seconds()),
            )
        )

    @m.output()
    def send_status_reconnecting(self):
        self._maybe_send_status(
            evolve(
                self._latest_status,
                peer_connection=ReconnectingPeer(self._reactor.seconds()),
            )
        )

    @m.output()
    def send_status_dilation_generation(self):
        # send_dilation_generation has just run recently, incrementing
        # this; "current status" is thus the prior value
        dilation_generation = self._next_dilation_generation - 1
        self._maybe_send_status(
            evolve(
                self._latest_status,
                generation=dilation_generation,
            )
        )

    @m.output()
    def send_status_stopped(self):
        self._maybe_send_status(
            evolve(
                self._latest_status,
                peer_connection=StoppedPeer(),
            )
        )

    # We are born WAITING after the local app calls w.dilate(). We enter
    # WANTING (and send a PLEASE) when we learn of a mutually-compatible
    # dilation_version.
    WAITING.upon(start, enter=WANTING, outputs=[send_please, send_status_dilation_generation])

    # we start CONNECTING when we get rx_PLEASE
    WANTING.upon(rx_PLEASE, enter=CONNECTING,
                 outputs=[choose_role, start_connecting_ignore_message, send_status_connecting])

    CONNECTING.upon(connection_made, enter=CONNECTED, outputs=[])

    # Leader
    CONNECTED.upon(connection_lost_leader, enter=FLUSHING,
                   outputs=[send_reconnect, send_status_dilation_generation, send_status_reconnecting])
    FLUSHING.upon(rx_RECONNECTING, enter=CONNECTING,
                  outputs=[start_connecting, send_status_reconnecting])

    # Follower
    # if we notice a lost connection, just wait for the Leader to notice too
    CONNECTED.upon(connection_lost_follower, enter=LONELY, outputs=[])
    LONELY.upon(rx_RECONNECT, enter=CONNECTING,
                outputs=[send_reconnecting, start_connecting,
                         send_status_dilation_generation, send_status_reconnecting])
    # but if they notice it first, abandon our (seemingly functional)
    # connection, then tell them that we're ready to try again
    CONNECTED.upon(rx_RECONNECT, enter=ABANDONING, outputs=[abandon_connection])
    ABANDONING.upon(connection_lost_follower, enter=CONNECTING,
                    outputs=[send_reconnecting, start_connecting,
                             send_status_dilation_generation, send_status_reconnecting])
    # and if they notice a problem while we're still connecting, abandon our
    # incomplete attempt and try again. in this case we don't have to wait
    # for a connection to finish shutdown
    CONNECTING.upon(rx_RECONNECT, enter=CONNECTING,
                    outputs=[stop_connecting,
                             send_reconnecting,
                             start_connecting,
                             send_status_dilation_generation,
                             send_status_reconnecting])

    # rx_HINTS never changes state, they're just accepted or ignored
    WANTING.upon(rx_HINTS, enter=WANTING, outputs=[])  # too early
    CONNECTING.upon(rx_HINTS, enter=CONNECTING, outputs=[use_hints])
    CONNECTED.upon(rx_HINTS, enter=CONNECTED, outputs=[])  # too late, ignore
    FLUSHING.upon(rx_HINTS, enter=FLUSHING, outputs=[])  # stale, ignore
    LONELY.upon(rx_HINTS, enter=LONELY, outputs=[])  # stale, ignore
    ABANDONING.upon(rx_HINTS, enter=ABANDONING, outputs=[])  # shouldn't happen
    STOPPING.upon(rx_HINTS, enter=STOPPING, outputs=[])

    WAITING.upon(stop, enter=STOPPED, outputs=[notify_stopped])
    WANTING.upon(stop, enter=STOPPED, outputs=[notify_stopped])
    CONNECTING.upon(stop, enter=STOPPED, outputs=[stop_connecting, notify_stopped, send_status_stopped])
    CONNECTED.upon(stop, enter=STOPPING, outputs=[abandon_connection])
    ABANDONING.upon(stop, enter=STOPPING, outputs=[])
    FLUSHING.upon(stop, enter=STOPPED, outputs=[notify_stopped, send_status_stopped])
    LONELY.upon(stop, enter=STOPPED, outputs=[notify_stopped, send_status_stopped])
    STOPPING.upon(connection_lost_leader, enter=STOPPED, outputs=[notify_stopped, send_status_stopped])
    STOPPING.upon(connection_lost_follower, enter=STOPPED, outputs=[notify_stopped, send_status_stopped])


@attrs
@implementer(IDilator)
class Dilator:
    """I launch the dilation process.

    I am created with every Wormhole (regardless of whether .dilate()
    was called or not), and I handle the initial phase of dilation,
    before we know whether we'll be the Leader or the Follower. Once we
    hear the other side's VERSION message (which tells us that we have a
    connection, they are capable of dilating, and which side we're on),
    then we build a Manager and hand control to it.
    """

    _reactor = attrib()
    _eventual_queue = attrib()
    _cooperator = attrib()
    _acceptable_versions = attrib()
    _did_dilate = attrib(init=False)

    def __attrs_post_init__(self):
        self._manager = None
        self._pending_dilation_key = None
        self._pending_wormhole_versions = None
        self._pending_inbound_dilate_messages = deque()
        self._did_dilate = Once(CanOnlyDilateOnceError)

    def wire(self, sender, terminator):
        self._S = ISend(sender)
        self._T = ITerminator(terminator)

    # this is the primary entry point, called when w.dilate() is
    # invoked; upstream calls are basically just call-through -- so
    # all these inputs should be validated.
    def dilate(self, transit_relay_location=None, no_listen=False, wormhole_status=None, status_update=None,
               ping_interval=None, expected_subprotocols=None):
        # ensure users can only call this API once -- in the past, it
        # was possible to call the API more than once but any cal
        # after the first would have no real effect:
        # transit_relay_location, no_listen, etc would all remain
        # unchanged)
        self._did_dilate()

        if self._manager is None:
            # build the manager right away, and tell it later when the
            # VERSIONS message arrives, and also when the dilation_key is set
            my_dilation_side = make_side()
            m = Manager(
                self._S,
                my_dilation_side,
                transit_relay_location,
                self._reactor,
                self._eventual_queue,
                self._cooperator,
                self._acceptable_versions,
                ping_interval or 30.0,
                expected_subprotocols,
                no_listen,
                status_update,
                initial_mailbox_status=wormhole_status,
            )
            self._manager = m
            if self._pending_dilation_key is not None:
                m.got_dilation_key(self._pending_dilation_key)
            if self._pending_wormhole_versions:
                m.got_wormhole_versions(self._pending_wormhole_versions)
            while self._pending_inbound_dilate_messages:
                plaintext = self._pending_inbound_dilate_messages.popleft()
                m.received_dilation_message(plaintext)

        return self._manager._api

    # Called by Terminator after everything else (mailbox, nameplate, server
    # connection) has shut down. Expects to fire T.stoppedD() when Dilator is
    # stopped too.
    def stop(self):
        if self._manager:
            self._manager.stop()
            # TODO: avoid Deferreds for control flow, hard to serialize
            self._manager.when_stopped().addCallback(lambda _: self._T.stoppedD())
        else:
            self._T.stoppedD()
            return
        # TODO: tolerate multiple calls

    # from Boss

    def got_key(self, key):
        # TODO: verify this happens before got_wormhole_versions, or add a gate
        # to tolerate either ordering
        purpose = b"dilation-v1"
        LENGTH = 32  # TODO: whatever Noise wants, I guess
        dilation_key = derive_key(key, purpose, LENGTH)
        if self._manager:
            self._manager.got_dilation_key(dilation_key)
        else:
            self._pending_dilation_key = dilation_key

    def got_wormhole_versions(self, their_wormhole_versions):
        if self._manager:
            self._manager.got_wormhole_versions(their_wormhole_versions)
        else:
            self._pending_wormhole_versions = their_wormhole_versions

    def received_dilate(self, plaintext):
        if not self._manager:
            self._pending_inbound_dilate_messages.append(plaintext)
        else:
            self._manager.received_dilation_message(plaintext)
