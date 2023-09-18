from __future__ import print_function, unicode_literals
import six
import os
from collections import deque
try:
    # py >= 3.3
    from collections.abc import Sequence
except ImportError:
    # py 2 and py3 < 3.3
    from collections import Sequence
from attr import attrs, attrib
from attr.validators import provides, instance_of, optional
from automat import MethodicalMachine
from zope.interface import implementer
from twisted.internet.defer import Deferred
from twisted.internet.interfaces import (IStreamClientEndpoint,
                                         IStreamServerEndpoint)
from twisted.python import log, failure
from .._interfaces import IDilator, IDilationManager, ISend, ITerminator
from ..util import dict_to_bytes, bytes_to_dict, bytes_to_hexstr
from ..observer import OneShotObserver
from .._key import derive_key
from .subchannel import (SubChannel, _SubchannelAddress, _WormholeAddress,
                         ControlEndpoint, SubchannelConnectorEndpoint,
                         SubchannelListenerEndpoint)
from .connector import Connector
from .._hints import parse_hint
from .roles import LEADER, FOLLOWER
from .connection import KCM, Ping, Pong, Open, Data, Close, Ack
from .inbound import Inbound
from .outbound import Outbound


# exported to Wormhole() for inclusion in versions message
# note that these are strings, not numbers, to facilitate
# experimentation or non-standard versions; the _order_ of versions in
# "can-dilate" is important!
DILATION_VERSIONS = ["1"]


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


@attrs
class EndpointRecord(Sequence):
    control = attrib(validator=provides(IStreamClientEndpoint))
    connect = attrib(validator=provides(IStreamClientEndpoint))
    listen = attrib(validator=provides(IStreamServerEndpoint))

    def __len__(self):
        return 3

    def __getitem__(self, n):
        return (self.control, self.connect, self.listen)[n]


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

@attrs(eq=False)
@implementer(IDilationManager)
class Manager(object):
    _S = attrib(validator=provides(ISend), repr=False)
    _my_side = attrib(validator=instance_of(type(u"")))
    _transit_relay_location = attrib(validator=optional(instance_of(str)))
    _reactor = attrib(repr=False)
    _eventual_queue = attrib(repr=False)
    _cooperator = attrib(repr=False)
    # TODO: can this validator work when the parameter is optional?
    _no_listen = attrib(validator=instance_of(bool), default=False)

    _dilation_key = None
    _tor = None  # TODO
    _timing = None  # TODO
    _next_subchannel_id = None  # initialized in choose_role
    _dilation_version = None  # initialized in got_wormhole_versions

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

        self._next_dilation_phase = 0

        # I kept getting confused about which methods were for inbound data
        # (and thus flow-control methods go "out") and which were for
        # outbound data (with flow-control going "in"), so I split them up
        # into separate pieces.
        self._inbound = Inbound(self, self._host_addr)
        self._outbound = Outbound(self, self._cooperator)  # from us to peer

        # We must open subchannel0 early, since messages may arrive very
        # quickly once the connection is established. This subchannel may or
        # may not ever get revealed to the caller, since the peer might not
        # even be capable of dilation.
        scid0 = 0
        peer_addr0 = _SubchannelAddress(scid0)
        sc0 = SubChannel(scid0, self, self._host_addr, peer_addr0)
        self._inbound.set_subchannel_zero(scid0, sc0)

        # we can open non-zero subchannels as soon as we get our first
        # connection, and we can make the Endpoints even earlier
        control_ep = ControlEndpoint(peer_addr0, sc0, self._eventual_queue)
        connect_ep = SubchannelConnectorEndpoint(self, self._host_addr, self._eventual_queue)
        listen_ep = SubchannelListenerEndpoint(self, self._host_addr, self._eventual_queue)
        # TODO: let inbound/outbound create the endpoints, then return them
        # to us
        self._inbound.set_listener_endpoint(listen_ep)

        self._endpoints = EndpointRecord(control_ep, connect_ep, listen_ep)

    def get_endpoints(self):
        return self._endpoints

    def got_dilation_key(self, key):
        assert isinstance(key, bytes)
        self._dilation_key = key

    def got_wormhole_versions(self, their_wormhole_versions):
        # this always happens before received_dilation_message
        self._dilation_version = None
        their_dilation_versions = set(their_wormhole_versions.get("can-dilate", []))
        my_versions = set(DILATION_VERSIONS)
        shared_versions = my_versions.intersection(their_dilation_versions)
        if "1" in shared_versions:
            self._dilation_version = "1"

        # dilation_version is the best mutually-compatible version we have
        # with the peer, or None if we have nothing in common

        if not self._dilation_version:  # "1" or None
            # TODO: be more specific about the error. dilation_version==None
            # means we had no version in common with them, which could either
            # be because they're so old they don't dilate at all, or because
            # they're so new that they no longer accommodate our old version
            self.fail(failure.Failure(OldPeerCannotDilateError()))

        self.start()

    def fail(self, f):
        self._endpoints.control._main_channel_failed(f)
        self._endpoints.connect._main_channel_failed(f)
        self._endpoints.listen._main_channel_failed(f)

    def received_dilation_message(self, plaintext):
        # this receives new in-order DILATE-n payloads, decrypted but not
        # de-JSONed.

        message = bytes_to_dict(plaintext)
        type = message["type"]
        if type == "please":
            self.rx_PLEASE(message)
        elif type == "connection-hints":
            self.rx_HINTS(message)
        elif type == "reconnect":
            self.rx_RECONNECT()
        elif type == "reconnecting":
            self.rx_RECONNECTING()
        else:
            log.err(UnknownDilationMessageType(message))
            return

    def when_stopped(self):
        return self._stopped.when_fired()

    def send_dilation_phase(self, **fields):
        dilation_phase = self._next_dilation_phase
        self._next_dilation_phase += 1
        self._S.send("dilate-%d" % dilation_phase, dict_to_bytes(fields))

    def send_hints(self, hints):  # from Connector
        self.send_dilation_phase(type="connection-hints", hints=hints)

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

    def send_open(self, scid):
        assert isinstance(scid, six.integer_types)
        self._queue_and_send(Open, scid)

    def send_data(self, scid, data):
        assert isinstance(scid, six.integer_types)
        self._queue_and_send(Data, scid, data)

    def send_close(self, scid):
        assert isinstance(scid, six.integer_types)
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
        self.connection_made()  # state machine update
        self._connection = c
        self._inbound.use_connection(c)
        self._outbound.use_connection(c)  # does c.registerProducer
        if not self._made_first_connection:
            self._made_first_connection = True
            self._endpoints.control._main_channel_ready()
            self._endpoints.connect._main_channel_ready()
            self._endpoints.listen._main_channel_ready()
        pass

    def connector_connection_lost(self):
        self._stop_using_connection()
        if self._my_role is LEADER:
            self.connection_lost_leader()  # state machine
        else:
            self.connection_lost_follower()

    def _stop_using_connection(self):
        # the connection is already lost by this point
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
                self._inbound.handle_open(r.scid)
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
            log.err(UnknownMessageType("{}".format(r)))

    # pings, pongs, and acks are not queued
    def send_ping(self, ping_id):
        self._outbound.send_if_connected(Ping(ping_id))

    def send_pong(self, ping_id):
        self._outbound.send_if_connected(Pong(ping_id))

    def send_ack(self, resp_seqnum):
        self._outbound.send_if_connected(Ack(resp_seqnum))

    def handle_ping(self, ping_id):
        self.send_pong(ping_id)

    def handle_pong(self, ping_id):
        # TODO: update is-alive timer
        pass

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
        self.send_dilation_phase(**msg)

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
        self.send_dilation_phase(type="reconnect")  # TODO: generation number?

    @m.output()
    def send_reconnecting(self):
        self.send_dilation_phase(type="reconnecting")  # TODO: generation?

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
        self._connection.disconnect()  # let connection_lost do cleanup

    @m.output()
    def notify_stopped(self):
        self._stopped.fire(None)

    # We are born WAITING after the local app calls w.dilate(). We enter
    # WANTING (and send a PLEASE) when we learn of a mutually-compatible
    # dilation_version.
    WAITING.upon(start, enter=WANTING, outputs=[send_please])

    # we start CONNECTING when we get rx_PLEASE
    WANTING.upon(rx_PLEASE, enter=CONNECTING,
                 outputs=[choose_role, start_connecting_ignore_message])

    CONNECTING.upon(connection_made, enter=CONNECTED, outputs=[])

    # Leader
    CONNECTED.upon(connection_lost_leader, enter=FLUSHING,
                   outputs=[send_reconnect])
    FLUSHING.upon(rx_RECONNECTING, enter=CONNECTING,
                  outputs=[start_connecting])

    # Follower
    # if we notice a lost connection, just wait for the Leader to notice too
    CONNECTED.upon(connection_lost_follower, enter=LONELY, outputs=[])
    LONELY.upon(rx_RECONNECT, enter=CONNECTING,
                outputs=[send_reconnecting, start_connecting])
    # but if they notice it first, abandon our (seemingly functional)
    # connection, then tell them that we're ready to try again
    CONNECTED.upon(rx_RECONNECT, enter=ABANDONING, outputs=[abandon_connection])
    ABANDONING.upon(connection_lost_follower, enter=CONNECTING,
                    outputs=[send_reconnecting, start_connecting])
    # and if they notice a problem while we're still connecting, abandon our
    # incomplete attempt and try again. in this case we don't have to wait
    # for a connection to finish shutdown
    CONNECTING.upon(rx_RECONNECT, enter=CONNECTING,
                    outputs=[stop_connecting,
                             send_reconnecting,
                             start_connecting])

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
    CONNECTING.upon(stop, enter=STOPPED, outputs=[stop_connecting, notify_stopped])
    CONNECTED.upon(stop, enter=STOPPING, outputs=[abandon_connection])
    ABANDONING.upon(stop, enter=STOPPING, outputs=[])
    FLUSHING.upon(stop, enter=STOPPED, outputs=[notify_stopped])
    LONELY.upon(stop, enter=STOPPED, outputs=[notify_stopped])
    STOPPING.upon(connection_lost_leader, enter=STOPPED, outputs=[notify_stopped])
    STOPPING.upon(connection_lost_follower, enter=STOPPED, outputs=[notify_stopped])


@attrs
@implementer(IDilator)
class Dilator(object):
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

    def __attrs_post_init__(self):
        self._manager = None
        self._pending_dilation_key = None
        self._pending_wormhole_versions = None
        self._pending_inbound_dilate_messages = deque()

    def wire(self, sender, terminator):
        self._S = ISend(sender)
        self._T = ITerminator(terminator)

    # this is the primary entry point, called when w.dilate() is invoked
    def dilate(self, transit_relay_location=None, no_listen=False):
        if not self._manager:
            # build the manager right away, and tell it later when the
            # VERSIONS message arrives, and also when the dilation_key is set
            my_dilation_side = make_side()
            m = Manager(self._S, my_dilation_side,
                        transit_relay_location,
                        self._reactor, self._eventual_queue,
                        self._cooperator, no_listen)
            self._manager = m
            if self._pending_dilation_key is not None:
                m.got_dilation_key(self._pending_dilation_key)
            if self._pending_wormhole_versions:
                m.got_wormhole_versions(self._pending_wormhole_versions)
            while self._pending_inbound_dilate_messages:
                plaintext = self._pending_inbound_dilate_messages.popleft()
                m.received_dilation_message(plaintext)
        return self._manager.get_endpoints()

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
