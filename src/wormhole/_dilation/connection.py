from collections import namedtuple
from attr import attrs, attrib
from attr.validators import instance_of
from automat import MethodicalMachine
from zope.interface import Interface, implementer
from twisted.python import log
from twisted.internet.protocol import Protocol
from twisted.internet.interfaces import ITransport
from .._interfaces import IDilationConnector
from ..observer import OneShotObserver
from ..util import provides
from .encode import to_be4, from_be4
from .roles import LEADER, FOLLOWER
from ._noise import NoiseInvalidMessage, NoiseHandshakeError, NOISE_MAX_PAYLOAD, NOISE_MAX_CIPHERTEXT

# InboundFraming is given data and returns Frames (Noise wire-side
# bytestrings). It handles the relay handshake and the prologue. The Frames it
# returns are either the ephemeral key (the Noise "handshake") or ciphertext
# messages.

# The next object up knows whether it's expecting a Handshake or a message. It
# feeds the first into Noise as a handshake, it feeds the rest into Noise as a
# message (which produces a plaintext stream). It emits tokens that are either
# "i've finished with the handshake (so you can send the KCM if you want)", or
# "here is a decrypted message (which might be the KCM)".

# the transmit direction goes directly to transport.write, and doesn't touch
# the state machine. we can do this because the way we encode/encrypt/frame
# things doesn't depend upon the receiver state. It would be more safe to e.g.
# prohibit sending ciphertext frames unless we're in the received-handshake
# state, but then we'll be in the middle of an inbound state transition ("we
# just received the handshake, so you can send a KCM now") when we perform an
# operation that depends upon the state (send_plaintext(kcm)), which is not a
# coherent/safe place to touch the state machine.

# we could set a flag and test it from inside send_plaintext, which kind of
# violates the state machine owning the state (ideally all "if" statements
# would be translated into same-input transitions from different starting
# states). For the specific question of sending plaintext frames, Noise will
# refuse us unless it's ready anyways, so the question is probably moot.


class IFramer(Interface):
    pass


class IRecord(Interface):
    pass


def first(seq):
    return seq[0]


class Disconnect(Exception):
    pass

# all connections look like:
# (step 1: only for outbound connections)
# 1: if we're connecting to a transit relay:
#    * send "sided relay handshake": "please relay TOKEN for side SIDE\n"
#    * the relay will send "ok\n" if/when our peer connects
#    * a non-relay will probably send junk
#    * wait for "ok\n", hang up if we get anything different
# (all subsequent steps are for both inbound and outbound connections)
# 2: send PROLOGUE_LEADER/FOLLOWER: "Magic-Wormhole Dilation Handshake v1 (l/f)\n\n"
# 3: wait for the opposite PROLOGUE string, else hang up
# (everything past this point is a Frame, with be4 length prefix. Frames are
#  either noise handshake or an encrypted message)
# 4: if LEADER, send noise handshake string. if FOLLOWER, wait for it
#    LEADER: m=n.write_message(), FOLLOWER: n.read_message(m)
# 5: if FOLLOWER, send noise response string. if LEADER, wait for it
#    FOLLOWER: m=n.write_message(), LEADER: n.read_message(m)
# 6: if FOLLOWER: send KCM (m=n.encrypt('')), wait for KCM (n.decrypt(m))
#    if LEADER: wait for KCM, gather viable connections, select
#               send KCM over selected connection, drop the rest
# 7: both: send Ping/Pong/Open/Data/Close/Ack records (n.encrypt(rec))


RelayOK = namedtuple("RelayOk", [])
Prologue = namedtuple("Prologue", [])
Frame = namedtuple("Frame", ["frame"])


@attrs
@implementer(IFramer)
class _Framer:
    _transport = attrib(validator=provides(ITransport))
    _outbound_prologue = attrib(validator=instance_of(bytes))
    _inbound_prologue = attrib(validator=instance_of(bytes))
    _buffer = b""
    _can_send_frames = False

    # in: use_relay
    # in: connectionMade, dataReceived
    # out: prologue_received, frame_received
    # out (shared): transport.loseConnection
    # out (shared): transport.write (relay handshake, prologue)
    # states: want_relay, want_prologue, want_frame
    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)  # pragma: no cover

    @m.state()
    def want_relay(self):
        pass  # pragma: no cover

    @m.state(initial=True)
    def want_prologue(self):
        pass  # pragma: no cover

    @m.state()
    def want_frame(self):
        pass  # pragma: no cover

    @m.input()
    def use_relay(self, relay_handshake):
        pass

    @m.input()
    def connectionMade(self):
        pass

    @m.input()
    def parse(self):
        pass

    @m.input()
    def got_relay_ok(self):
        pass

    @m.input()
    def got_prologue(self):
        pass

    @m.output()
    def store_relay_handshake(self, relay_handshake):
        self._outbound_relay_handshake = relay_handshake
        self._expected_relay_handshake = b"ok\n"  # TODO: make this configurable

    @m.output()
    def send_relay_handshake(self):
        self._transport.write(self._outbound_relay_handshake)

    @m.output()
    def send_prologue(self):
        self._transport.write(self._outbound_prologue)

    @m.output()
    def parse_relay_ok(self):
        if self._get_expected("relay_ok", self._expected_relay_handshake):
            return RelayOK()

    @m.output()
    def parse_prologue(self):
        if self._get_expected("prologue", self._inbound_prologue):
            return Prologue()

    @m.output()
    def can_send_frames(self):
        self._can_send_frames = True  # for assertion in send_frame()

    @m.output()
    def parse_frame(self):
        if len(self._buffer) < 4:
            return None
        frame_length = from_be4(self._buffer[0:4])
        if len(self._buffer) < 4 + frame_length:
            return None
        frame = self._buffer[4:4 + frame_length]
        self._buffer = self._buffer[4 + frame_length:]  # TODO: avoid copy
        return Frame(frame=frame)

    want_prologue.upon(use_relay, outputs=[store_relay_handshake],
                       enter=want_relay)

    want_relay.upon(connectionMade, outputs=[send_relay_handshake],
                    enter=want_relay)
    want_relay.upon(parse, outputs=[parse_relay_ok], enter=want_relay,
                    collector=first)
    want_relay.upon(got_relay_ok, outputs=[send_prologue], enter=want_prologue)

    want_prologue.upon(connectionMade, outputs=[send_prologue],
                       enter=want_prologue)
    want_prologue.upon(parse, outputs=[parse_prologue], enter=want_prologue,
                       collector=first)
    want_prologue.upon(got_prologue, outputs=[can_send_frames], enter=want_frame)

    want_frame.upon(parse, outputs=[parse_frame], enter=want_frame,
                    collector=first)

    def _get_expected(self, name, expected):
        lb = len(self._buffer)
        le = len(expected)
        if self._buffer.startswith(expected):
            # if the buffer starts with the expected string, consume it and
            # return True
            self._buffer = self._buffer[le:]
            return True
        if not expected.startswith(self._buffer):
            # we're not on track: the data we've received so far does not
            # match the expected value, so this can't possibly be right.
            # Don't complain until we see the expected length, or a newline,
            # so we can capture the weird input in the log for debugging.
            if (b"\n" in self._buffer or lb >= le):
                log.msg(f"bad {name}: {self._buffer[:le]}")
                raise Disconnect()
            return False  # wait a bit longer
        # good so far, just waiting for the rest
        return False

    # external API is: connectionMade, add_and_parse, and send_frame

    def add_and_parse(self, data):
        # we can't make this an @m.input because we can't change the state
        # from within an input. Instead, let the state choose the parser to
        # use, then use the parsed token to drive a state transition.
        self._buffer += data
        while True:
            # it'd be nice to use an iterator here, but since self.parse()
            # dispatches to a different parser (depending upon the current
            # state), we'd be using multiple iterators
            token = self.parse()
            if isinstance(token, RelayOK):
                self.got_relay_ok()
            elif isinstance(token, Prologue):
                self.got_prologue()
                yield token  # triggers send_handshake
            elif isinstance(token, Frame):
                yield token
            else:
                break

    def send_frame(self, frame):
        assert self._can_send_frames
        self._transport.write(to_be4(len(frame)) + frame)

# RelayOK: Newline-terminated buddy-is-connected response from Relay.
#          First data received from relay.
# Prologue: double-newline-terminated this-is-really-wormhole response
#           from peer. First data received from peer.
# Frame: Either handshake or encrypted message. Length-prefixed on wire.
# Handshake: the Noise ephemeral key, first framed message
# Message: plaintext: encoded KCM/PING/PONG/OPEN/DATA/CLOSE/ACK
# KCM: Key Confirmation Message (encrypted b"\x00"). First frame
#      from peer. Sent immediately by Follower, after Selection by Leader.
# Record: namedtuple of KCM/Open/Data/Close/Ack/Ping/Pong


Handshake = namedtuple("Handshake", [])
# decrypted frames: produces KCM, Ping, Pong, Open, Data, Close, Ack
KCM = namedtuple("KCM", [])
Ping = namedtuple("Ping", ["ping_id"])  # ping_id is arbitrary 4-byte value
Pong = namedtuple("Pong", ["ping_id"])
Open = namedtuple("Open", ["seqnum", "scid", "subprotocol"])  # seqnum is integer, subprotocol is str
Data = namedtuple("Data", ["seqnum", "scid", "data"])
Close = namedtuple("Close", ["seqnum", "scid"])  # scid is integer
Ack = namedtuple("Ack", ["resp_seqnum"])  # resp_seqnum is integer
Records = (KCM, Ping, Pong, Open, Data, Close, Ack)
Handshake_or_Records = (Handshake,) + Records

T_KCM = b"\x00"
T_PING = b"\x01"
T_PONG = b"\x02"
T_OPEN = b"\x03"
T_DATA = b"\x04"
T_CLOSE = b"\x05"
T_ACK = b"\x06"


def parse_record(plaintext):
    msgtype = plaintext[0:1]
    if msgtype == T_KCM:
        return KCM()
    if msgtype == T_PING:
        ping_id = plaintext[1:5]
        return Ping(ping_id)
    if msgtype == T_PONG:
        ping_id = plaintext[1:5]
        return Pong(ping_id)
    if msgtype == T_OPEN:
        scid = from_be4(plaintext[1:5])
        seqnum = from_be4(plaintext[5:9])
        subprotocol = str(plaintext[9:], "utf8")
        return Open(seqnum, scid, subprotocol)
    if msgtype == T_DATA:
        scid = from_be4(plaintext[1:5])
        seqnum = from_be4(plaintext[5:9])
        data = plaintext[9:]
        return Data(seqnum, scid, data)
    if msgtype == T_CLOSE:
        scid = from_be4(plaintext[1:5])
        seqnum = from_be4(plaintext[5:9])
        return Close(seqnum, scid)
    if msgtype == T_ACK:
        resp_seqnum = from_be4(plaintext[1:5])
        return Ack(resp_seqnum)
    log.err(f"received unknown message type: {plaintext}")
    raise ValueError()


def encode_record(r):
    if isinstance(r, KCM):
        return T_KCM
    if isinstance(r, Ping):
        return T_PING + r.ping_id
    if isinstance(r, Pong):
        return T_PONG + r.ping_id
    if isinstance(r, Open):
        assert isinstance(r.scid, int)
        assert isinstance(r.seqnum, int)
        assert isinstance(r.subprotocol, str)
        return T_OPEN + to_be4(r.scid) + to_be4(r.seqnum) + r.subprotocol.encode("utf8")
    if isinstance(r, Data):
        assert isinstance(r.scid, int)
        assert isinstance(r.seqnum, int)
        return T_DATA + to_be4(r.scid) + to_be4(r.seqnum) + r.data
    if isinstance(r, Close):
        assert isinstance(r.scid, int)
        assert isinstance(r.seqnum, int)
        return T_CLOSE + to_be4(r.scid) + to_be4(r.seqnum)
    if isinstance(r, Ack):
        assert isinstance(r.resp_seqnum, int)
        return T_ACK + to_be4(r.resp_seqnum)
    raise TypeError(r)


def _is_role(_record, _attr, value):
    if value not in [LEADER, FOLLOWER]:
        raise ValueError("role must be LEADER or FOLLOWER")


@attrs
@implementer(IRecord)
class _Record:
    _framer = attrib(validator=provides(IFramer))
    _noise = attrib()

    _role = attrib(default="unspecified", validator=_is_role)  # for debugging

    n = MethodicalMachine()
    # TODO: set_trace

    def __attrs_post_init__(self):
        self._noise.start_handshake()

    # in: role=
    # in: prologue_received, frame_received
    # out: handshake_received, record_received
    # out: transport.write (noise handshake, encrypted records)
    # states: want_prologue, want_handshake, want_record

    @n.state(initial=True)
    def no_role_set(self):
        pass  # pragma: no cover

    @n.state()
    def want_prologue_leader(self):
        pass  # pragma: no cover

    @n.state()
    def want_prologue_follower(self):
        pass  # pragma: no cover

    @n.state()
    def want_handshake_leader(self):
        pass  # pragma: no cover

    @n.state()
    def want_handshake_follower(self):
        pass  # pragma: no cover

    @n.state()
    def want_message(self):
        pass  # pragma: no cover

    @n.input()
    def set_role_leader(self):
        pass

    @n.input()
    def set_role_follower(self):
        pass

    @n.input()
    def got_prologue(self):
        pass

    @n.input()
    def got_frame(self, frame):
        pass

    @n.output()
    def ignore_and_send_handshake(self, frame):
        self._send_handshake()

    @n.output()
    def send_handshake(self):
        self._send_handshake()

    def _send_handshake(self):
        try:
            handshake = self._noise.write_message()  # generate the ephemeral key
        except NoiseHandshakeError as e:
            log.err(e, "noise error during handshake")
            raise
        self._framer.send_frame(handshake)

    @n.output()
    def process_handshake(self, frame):
        try:
            payload = self._noise.read_message(frame)
            # Noise can include unencrypted data in the handshake, but we don't
            # use it
            del payload
        except NoiseInvalidMessage as e:
            log.err(e, "bad inbound noise handshake")
            raise Disconnect()
        return Handshake()

    @n.output()
    def decrypt_message(self, frame):
        # opposite of the encoding: if we have _more_ than what a
        # single Noise packet can hold, we have to build up the real
        # plaintext incrementally.
        size = len(frame)
        try:
            if size <= NOISE_MAX_CIPHERTEXT:
                message = self._noise.decrypt(frame)
            else:
                start = 0
                message = b""
                while start < size:
                    ciphertext = frame[start:start + NOISE_MAX_CIPHERTEXT]
                    message += self._noise.decrypt(ciphertext)
                    start += NOISE_MAX_CIPHERTEXT
        except NoiseInvalidMessage as e:
            # if this happens during tests, flunk the test
            log.err(e, "bad inbound noise frame")
            raise Disconnect()
        return parse_record(message)

    no_role_set.upon(set_role_leader, outputs=[], enter=want_prologue_leader)
    want_prologue_leader.upon(got_prologue, outputs=[send_handshake],
                              enter=want_handshake_leader)
    want_handshake_leader.upon(got_frame, outputs=[process_handshake],
                               collector=first, enter=want_message)

    no_role_set.upon(set_role_follower, outputs=[], enter=want_prologue_follower)
    want_prologue_follower.upon(got_prologue, outputs=[],
                                enter=want_handshake_follower)
    want_handshake_follower.upon(got_frame, outputs=[process_handshake,
                                                     ignore_and_send_handshake],
                                 collector=first, enter=want_message)

    want_message.upon(got_frame, outputs=[decrypt_message],
                      collector=first, enter=want_message)

    # external API is: connectionMade, dataReceived, send_record

    def connectionMade(self):
        self._framer.connectionMade()

    def add_and_unframe(self, data):
        for token in self._framer.add_and_parse(data):
            if isinstance(token, Prologue):
                self.got_prologue()  # triggers send_handshake
            else:
                assert isinstance(token, Frame)
                yield self.got_frame(token.frame)  # Handshake or a Record type

    def send_record(self, r):
        message = encode_record(r)
        if len(message) <= NOISE_MAX_PAYLOAD:
            frame = self._noise.encrypt(message)
        else:
            # we want to put all the encrypted bytes into one "frame",
            # but there are more bytes than we can fit in a Noise
            # message .. so we chop them up
            start = 0
            frame = b""
            while start < len(message):
                this_msg = message[start:start + NOISE_MAX_PAYLOAD]
                cip = self._noise.encrypt(this_msg)
                frame += cip
                start += NOISE_MAX_PAYLOAD
        self._framer.send_frame(frame)


@attrs(eq=False)
class DilatedConnectionProtocol(Protocol):
    """I manage an L2 connection.

    When a new L2 connection is needed (as determined by the Leader),
    both Leader and Follower will initiate many simultaneous connections
    (probably TCP, but conceivably others). A subset will actually
    connect. A subset of those will successfully pass negotiation by
    exchanging handshakes to demonstrate knowledge of the session key.
    One of the negotiated connections will be selected by the Leader for
    active use, and the others will be dropped.

    At any given time, there is at most one active L2 connection.
    """

    _eventual_queue = attrib(repr=False)
    _role = attrib()
    _description = attrib()
    _connector = attrib(validator=provides(IDilationConnector), repr=False)
    _noise = attrib(repr=False)
    _outbound_prologue = attrib(validator=instance_of(bytes), repr=False)
    _inbound_prologue = attrib(validator=instance_of(bytes), repr=False)

    _use_relay = False
    _relay_handshake = None

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        self._manager = None  # set if/when we are selected
        self._disconnected = OneShotObserver(self._eventual_queue)
        self._can_send_records = False
        self._inbound_record_queue = []

    @m.state(initial=True)
    def unselected(self):
        pass  # pragma: no cover

    @m.state()
    def selecting(self):
        pass  # pragma: no cover

    @m.state()
    def selected(self):
        pass  # pragma: no cover

    @m.input()
    def got_kcm(self):
        pass

    @m.input()
    def select(self, manager):
        pass  # fires set_manager()

    @m.input()
    def got_record(self, record):
        pass

    @m.output()
    def add_candidate(self):
        self._connector.add_candidate(self)

    @m.output()
    def queue_inbound_record(self, record):
        # the Follower will see a dataReceived chunk containing both the KCM
        # (leader says we've been picked) and the first record.
        # Connector.consider takes an eventual-turn to decide to accept this
        # connection, which means the record will arrive before we get
        # .select() and move to the 'selected' state where we can
        # deliver_record. So we need to queue the record for a turn. TODO:
        # when we move to the sans-io event-driven scheme, this queue
        # shouldn't be necessary
        self._inbound_record_queue.append(record)

    @m.output()
    def set_manager(self, manager):
        self._manager = manager
        self.when_disconnected().addCallback(lambda c:
                                             manager.connector_connection_lost())

    @m.output()
    def send_status_have_peer(self, manager):
        assert self._manager is not None, "_manager must be set by now"
        self._manager.have_peer(self)

    @m.output()
    def can_send_records(self, manager):
        self._can_send_records = True

    @m.output()
    def process_inbound_queue(self, manager):
        while self._inbound_record_queue:
            r = self._inbound_record_queue.pop(0)
            self._manager.got_record(r)

    @m.output()
    def deliver_record(self, record):
        self._manager.got_record(record)

    unselected.upon(got_kcm, outputs=[add_candidate], enter=selecting)
    selecting.upon(got_record, outputs=[queue_inbound_record], enter=selecting)
    selecting.upon(select,
                   outputs=[set_manager, send_status_have_peer, can_send_records, process_inbound_queue],
                   enter=selected)
    selected.upon(got_record, outputs=[deliver_record], enter=selected)

    # called by Connector

    def use_relay(self, relay_handshake):
        assert isinstance(relay_handshake, bytes)
        self._use_relay = True
        self._relay_handshake = relay_handshake

    def when_disconnected(self):
        return self._disconnected.when_fired()

    def disconnect(self):
        self.transport.loseConnection()

    # select() called by Connector

    # called by Manager
    def send_record(self, record):
        assert self._can_send_records
        self._record.send_record(record)

    # IProtocol methods

    def connectionMade(self):
        try:
            framer = _Framer(self.transport,
                             self._outbound_prologue, self._inbound_prologue)
            if self._use_relay:
                framer.use_relay(self._relay_handshake)
            self._record = _Record(framer, self._noise, self._role)
            if self._role is LEADER:
                self._record.set_role_leader()
            else:
                self._record.set_role_follower()
            self._record.connectionMade()

        except:  # noqa
            log.err()
            raise

    def dataReceived(self, data):
        try:
            for token in self._record.add_and_unframe(data):
                assert isinstance(token, Handshake_or_Records)
                if isinstance(token, Handshake):
                    if self._role is FOLLOWER:
                        self._record.send_record(KCM())
                elif isinstance(token, KCM):
                    # if we're the leader, add this connection as a candidate.
                    # if we're the follower, accept this connection.
                    self.got_kcm()  # connector.add_candidate()
                else:
                    self.got_record(token)  # manager.got_record()
        except Disconnect:
            self.transport.loseConnection()

    def connectionLost(self, why=None):
        self._disconnected.fire(self)
