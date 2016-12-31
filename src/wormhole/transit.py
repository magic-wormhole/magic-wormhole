# no unicode_literals, revisit after twisted patch
from __future__ import print_function, absolute_import
import os, re, sys, time, socket
from collections import namedtuple, deque
from binascii import hexlify, unhexlify
import six
from zope.interface import implementer
from twisted.python import log
from twisted.python.runtime import platformType
from twisted.internet import (reactor, interfaces, defer, protocol,
                              endpoints, task, address, error)
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.protocols import policies
from nacl.secret import SecretBox
from hkdf import Hkdf
from .errors import InternalError
from .timing import DebugTiming
from .util import bytes_to_hexstr
from . import ipaddrs

def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    return Hkdf(salt, skm).expand(CTXinfo, outlen)

class TransitError(Exception):
    pass

class BadHandshake(Exception):
    pass

class TransitClosed(TransitError):
    pass

class BadNonce(TransitError):
    pass

# The beginning of each TCP connection consists of the following handshake
# messages. The sender transmits the same text regardless of whether it is on
# the initiating/connecting end of the TCP connection, or on the
# listening/accepting side. Same for the receiver.
#
#  sender -> receiver: transit sender TXID_HEX ready\n\n
#  receiver -> sender: transit receiver RXID_HEX ready\n\n
#
# Any deviations from this result in the socket being closed. The handshake
# messages are designed to provoke an invalid response from other sorts of
# servers (HTTP, SMTP, echo).
#
# If the sender is satisfied with the handshake, and this is the first socket
# to complete negotiation, the sender does:
#
#  sender -> receiver: go\n
#
# and the next byte on the wire will be from the application.
#
# If this is not the first socket, the sender does:
#
#  sender -> receiver: nevermind\n
#
# and closes the socket.

# So the receiver looks for "transit sender TXID_HEX ready\n\ngo\n" and hangs
# up upon the first wrong byte. The sender lookgs for "transit receiver
# RXID_HEX ready\n\n" and then makes a first/not-first decision about sending
# "go\n" or "nevermind\n"+close().

def build_receiver_handshake(key):
    hexid = HKDF(key, 32, CTXinfo=b"transit_receiver")
    return b"transit receiver "+hexlify(hexid)+b" ready\n\n"

def build_sender_handshake(key):
    hexid = HKDF(key, 32, CTXinfo=b"transit_sender")
    return b"transit sender "+hexlify(hexid)+b" ready\n\n"

def build_sided_relay_handshake(key, side):
    assert isinstance(side, type(u""))
    assert len(side) == 8*2
    token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
    return b"please relay "+hexlify(token)+b" for side "+side.encode("ascii")+b"\n"


# These namedtuples are "hint objects". The JSON-serializable dictionaries
# are "hint dicts".

# DirectTCPV1Hint and TorTCPV1Hint mean the following protocol:
# * make a TCP connection (possibly via Tor)
# * send the sender/receiver handshake bytes first
# * expect to see the receiver/sender handshake bytes from the other side
# * the sender writes "go\n", the receiver waits for "go\n"
# * the rest of the connection contains transit data
DirectTCPV1Hint = namedtuple("DirectTCPV1Hint", ["hostname", "port", "priority"])
TorTCPV1Hint = namedtuple("TorTCPV1Hint", ["hostname", "port", "priority"])
# RelayV1Hint contains a tuple of DirectTCPV1Hint and TorTCPV1Hint hints (we
# use a tuple rather than a list so they'll be hashable into a set). For each
# one, make the TCP connection, send the relay handshake, then complete the
# rest of the V1 protocol. Only one hint per relay is useful.
RelayV1Hint = namedtuple("RelayV1Hint", ["hints"])

def describe_hint_obj(hint):
    if isinstance(hint, DirectTCPV1Hint):
        return u"tcp:%s:%d" % (hint.hostname, hint.port)
    elif isinstance(hint, TorTCPV1Hint):
        return u"tor:%s:%d" % (hint.hostname, hint.port)
    else:
        return str(hint)

def parse_hint_argv(hint, stderr=sys.stderr):
    assert isinstance(hint, type(u""))
    # return tuple or None for an unparseable hint
    priority = 0.0
    mo = re.search(r'^([a-zA-Z0-9]+):(.*)$', hint)
    if not mo:
        print("unparseable hint '%s'" % (hint,), file=stderr)
        return None
    hint_type = mo.group(1)
    if hint_type != "tcp":
        print("unknown hint type '%s' in '%s'" % (hint_type, hint), file=stderr)
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

TIMEOUT=15

@implementer(interfaces.IProducer, interfaces.IConsumer)
class Connection(protocol.Protocol, policies.TimeoutMixin):
    def __init__(self, owner, relay_handshake, start, description):
        self.state = "too-early"
        self.buf = b""
        self.owner = owner
        self.relay_handshake = relay_handshake
        self.start = start
        self._description = description
        self._negotiation_d = defer.Deferred(self._cancel)
        self._error = None
        self._consumer = None
        self._consumer_bytes_written = 0
        self._consumer_bytes_expected = None
        self._consumer_deferred = None
        self._inbound_records = deque()
        self._waiting_reads = deque()

    def connectionMade(self):
        self.setTimeout(TIMEOUT) # does timeoutConnection() when it expires
        self.factory.connectionWasMade(self)

    def startNegotiation(self):
        if self.relay_handshake is not None:
            self.transport.write(self.relay_handshake)
            self.state = "relay"
        else:
            self.state = "start"
        self.dataReceived(b"") # cycle the state machine
        return self._negotiation_d

    def _cancel(self, d):
        self.state = "hung up" # stop reacting to anything further
        self._error = defer.CancelledError()
        self.transport.loseConnection()
        # if connectionLost isn't called synchronously, then our
        # self._negotiation_d will have been errbacked by Deferred.cancel
        # (which is our caller). So if it's still around, clobber it
        if self._negotiation_d:
            self._negotiation_d = None


    def dataReceived(self, data):
        try:
            self._dataReceived(data)
        except Exception as e:
            self.setTimeout(None)
            self._error = e
            self.transport.loseConnection()
            self.state = "hung up"
            if not isinstance(e, BadHandshake):
                raise

    def _check_and_remove(self, expected):
        # any divergence is a handshake error
        if not self.buf.startswith(expected[:len(self.buf)]):
            raise BadHandshake("got %r want %r" % (self.buf, expected))
        if len(self.buf) < len(expected):
            return False # keep waiting
        self.buf = self.buf[len(expected):]
        return True

    def _dataReceived(self, data):
        # protocol is:
        #  (maybe: send relay handshake, wait for ok)
        #  send (send|receive)_handshake
        #  wait for (receive|send)_handshake
        #  sender: decide, send "go" or hang up
        #  receiver: wait for "go"
        self.buf += data

        assert self.state != "too-early"
        if self.state == "relay":
            if not self._check_and_remove(b"ok\n"):
                return
            self.state = "start"
        if self.state == "start":
            self.transport.write(self.owner._send_this())
            self.state = "handshake"
        if self.state == "handshake":
            if not self._check_and_remove(self.owner._expect_this()):
                return
            self.state = self.owner.connection_ready(self)
            # If we're the receiver, we'll be moved to state
            # "wait-for-decision", which means we're waiting for the other
            # side (the sender) to make a decision. If we're the sender,
            # we'll either be moved to state "go" (send GO and move directly
            # to state "records") or state "nevermind" (send NEVERMIND and
            # hang up).

        if self.state == "wait-for-decision":
            if not self._check_and_remove(b"go\n"):
                return
            self._negotiationSuccessful()
        if self.state == "go":
            GO = b"go\n"
            self.transport.write(GO)
            self._negotiationSuccessful()
        if self.state == "nevermind":
            self.transport.write(b"nevermind\n")
            raise BadHandshake("abandoned")
        if self.state == "records":
            return self.dataReceivedRECORDS()
        if isinstance(self.state, Exception): # for tests
            raise self.state

    def _negotiationSuccessful(self):
        self.state = "records"
        self.setTimeout(None)
        send_key = self.owner._sender_record_key()
        self.send_box = SecretBox(send_key)
        self.send_nonce = 0
        receive_key = self.owner._receiver_record_key()
        self.receive_box = SecretBox(receive_key)
        self.next_receive_nonce = 0
        d, self._negotiation_d = self._negotiation_d, None
        d.callback(self)

    def dataReceivedRECORDS(self):
        while True:
            if len(self.buf) < 4:
                return
            length = int(hexlify(self.buf[:4]), 16)
            if len(self.buf) < 4+length:
                return
            encrypted, self.buf = self.buf[4:4+length], self.buf[4+length:]

            record = self._decrypt_record(encrypted)
            self.recordReceived(record)

    def _decrypt_record(self, encrypted):
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE] # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        if nonce != self.next_receive_nonce:
            raise BadNonce("received out-of-order record: got %d, expected %d"
                           % (nonce, self.next_receive_nonce))
        self.next_receive_nonce += 1
        record = self.receive_box.decrypt(encrypted)
        return record

    def describe(self):
        return self._description

    def send_record(self, record):
        if not isinstance(record, type(b"")): raise InternalError
        assert SecretBox.NONCE_SIZE == 24
        assert self.send_nonce < 2**(8*24)
        assert len(record) < 2**(8*4)
        nonce = unhexlify("%048x" % self.send_nonce) # big-endian
        self.send_nonce += 1
        encrypted = self.send_box.encrypt(record, nonce)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        self.transport.write(length)
        self.transport.write(encrypted)

    def recordReceived(self, record):
        if self._consumer:
            self._writeToConsumer(record)
            return
        self._inbound_records.append(record)
        self._deliverRecords()

    def receive_record(self):
        d = defer.Deferred()
        self._waiting_reads.append(d)
        self._deliverRecords()
        return d

    def _deliverRecords(self):
        while self._inbound_records and self._waiting_reads:
            r = self._inbound_records.popleft()
            d = self._waiting_reads.popleft()
            d.callback(r)

    def close(self):
        self.transport.loseConnection()
        while self._waiting_reads:
            d = self._waiting_reads.popleft()
            d.errback(error.ConnectionClosed())

    def timeoutConnection(self):
        self._error = BadHandshake("timeout")
        self.transport.loseConnection()

    def connectionLost(self, reason=None):
        self.setTimeout(None)
        d, self._negotiation_d = self._negotiation_d, None
        # the Deferred is only relevant until negotiation finishes, so skip
        # this if it's alredy been fired
        if d:
            # Each call to loseConnection() sets self._error first, so we can
            # deliver useful information to the Factory that's waiting on
            # this (although they'll generally ignore the specific error,
            # except for logging unexpected ones). The possible cases are:
            #
            # cancel: defer.CancelledError
            # far-end disconnect: BadHandshake("connection lost")
            # handshake error (something we didn't like): BadHandshake(what)
            # other error: some other Exception
            # timeout: BadHandshake("timeout")

            d.errback(self._error or BadHandshake("connection lost"))
        if self._consumer_deferred:
            self._consumer_deferred.errback(error.ConnectionClosed())

    # IConsumer methods, for outbound flow-control. We pass these through to
    # the transport. The 'producer' is something like a t.p.basic.FileSender
    def registerProducer(self, producer, streaming):
        assert interfaces.IConsumer.providedBy(self.transport)
        self.transport.registerProducer(producer, streaming)
    def unregisterProducer(self):
        self.transport.unregisterProducer()
    def write(self, data):
        self.send_record(data)

    # IProducer methods, for inbound flow-control. We pass these through to
    # the transport.
    def stopProducing(self):
        self.transport.stopProducing()
    def pauseProducing(self):
        self.transport.pauseProducing()
    def resumeProducing(self):
        self.transport.resumeProducing()

    # Helper methods

    def connectConsumer(self, consumer, expected=None):
        """Helper method to glue an instance of e.g. t.p.ftp.FileConsumer to
        us. Inbound records will be written as bytes to the consumer.

        Set 'expected' to an integer to automatically disconnect when at
        least that number of bytes have been written. This function will then
        return a Deferred (that fires with the number of bytes actually
        received). If the connection is lost while this Deferred is
        outstanding, it will errback.

        If 'expected' is None, then this function returns None instead of a
        Deferred, and you must call disconnectConsumer() when you are done."""

        if self._consumer:
            raise RuntimeError("A consumer is already attached: %r" %
                               self._consumer)

        # be aware of an ordering hazard: when we call the consumer's
        # .registerProducer method, they are likely to immediately call
        # self.resumeProducing, which we'll deliver to self.transport, which
        # might call our .dataReceived, which may cause more records to be
        # available. By waiting to set self._consumer until *after* we drain
        # any pending records, we avoid delivering records out of order,
        # which would be bad.
        consumer.registerProducer(self, True)
        # There might be enough data queued to exceed 'expected' before we
        # leave this function. We must be sure to register the producer
        # before it gets unregistered.

        self._consumer = consumer
        self._consumer_bytes_written = 0
        self._consumer_bytes_expected = expected
        d = None
        if expected is not None:
            d = defer.Deferred()
        self._consumer_deferred = d
        # drain any pending records
        while self._consumer and self._inbound_records:
            r = self._inbound_records.popleft()
            self._writeToConsumer(r)
        return d

    def _writeToConsumer(self, record):
        self._consumer.write(record)
        self._consumer_bytes_written += len(record)
        if self._consumer_bytes_expected is not None:
            if self._consumer_bytes_written >= self._consumer_bytes_expected:
                d = self._consumer_deferred
                self.disconnectConsumer()
                d.callback(self._consumer_bytes_written)

    def disconnectConsumer(self):
        self._consumer.unregisterProducer()
        self._consumer = None
        self._consumer_bytes_expected = None
        self._consumer_deferred = None

    # Helper method to write a known number of bytes to a file. This has no
    # flow control: the filehandle cannot push back. 'progress' is an
    # optional callable which will be called on each write (with the number
    # of bytes written). Returns a Deferred that fires (with the number of
    # bytes written) when the count is reached or the RecordPipe is closed.
    def writeToFile(self, f, expected, progress=None, hasher=None):
        fc = FileConsumer(f, progress, hasher)
        return self.connectConsumer(fc, expected)

class OutboundConnectionFactory(protocol.ClientFactory):
    protocol = Connection

    def __init__(self, owner, relay_handshake, description):
        self.owner = owner
        self.relay_handshake = relay_handshake
        self._description = description
        self.start = time.time()

    def buildProtocol(self, addr):
        p = self.protocol(self.owner, self.relay_handshake, self.start,
                          self._description)
        p.factory = self
        return p

    def connectionWasMade(self, p):
        # outbound connections are handled via the endpoint
        pass


class InboundConnectionFactory(protocol.ClientFactory):
    protocol = Connection

    def __init__(self, owner):
        self.owner = owner
        self.start = time.time()
        self._inbound_d = defer.Deferred(self._cancel)
        self._pending_connections = set()

    def whenDone(self):
        return self._inbound_d

    def _cancel(self, inbound_d):
        self._shutdown()
        # our _inbound_d will be errbacked by Deferred.cancel()

    def _shutdown(self):
        for d in list(self._pending_connections):
            d.cancel() # that fires _remove and _proto_failed

    def _describePeer(self, addr):
        if isinstance(addr, address.HostnameAddress):
            return "<-%s:%d" % (addr.hostname, addr.port)
        elif isinstance(addr, (address.IPv4Address, address.IPv6Address)):
            return "<-%s:%d" % (addr.host, addr.port)
        return "<-%r" % addr

    def buildProtocol(self, addr):
        p = self.protocol(self.owner, None, self.start,
                          self._describePeer(addr))
        p.factory = self
        return p

    def connectionWasMade(self, p):
        d = p.startNegotiation()
        self._pending_connections.add(d)
        d.addBoth(self._remove, d)
        d.addCallbacks(self._proto_succeeded, self._proto_failed)

    def _remove(self, res, d):
        self._pending_connections.remove(d)
        return res

    def _proto_succeeded(self, p):
        self._shutdown()
        self._inbound_d.callback(p)

    def _proto_failed(self, f):
        # ignore these two, let Twisted log everything else
        f.trap(BadHandshake, defer.CancelledError)
        pass

def allocate_tcp_port():
    """Return an (integer) available TCP port on localhost. This briefly
    listens on the port in question, then closes it right away."""
    # We want to bind() the socket but not listen(). Twisted (in
    # tcp.Port.createInternetSocket) would do several other things:
    # non-blocking, close-on-exec, and SO_REUSEADDR. We don't need
    # non-blocking because we never listen on it, and we don't need
    # close-on-exec because we close it right away. So just add SO_REUSEADDR.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if platformType == "posix" and sys.platform != "cygwin":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

class _ThereCanBeOnlyOne:
    """Accept a list of contender Deferreds, and return a summary Deferred.
    When the first contender fires successfully, cancel the rest and fire the
    summary with the winning contender's result. If all error, errback the
    summary.

    status_cb=?
    """
    def __init__(self, contenders):
        self._remaining = set(contenders)
        self._winner_d = defer.Deferred(self._cancel)
        self._first_success = None
        self._first_failure = None
        self._have_winner = False
        self._fired = False

    def _cancel(self, _):
        for d in list(self._remaining):
            d.cancel()
        # since that will errback everything in _remaining, we'll have hit
        # _maybe_done() and fired self._winner_d by this point

    def run(self):
        for d in list(self._remaining):
            d.addBoth(self._remove, d)
            d.addCallbacks(self._succeeded, self._failed)
            d.addCallback(self._maybe_done)
        return self._winner_d

    def _remove(self, res, d):
        self._remaining.remove(d)
        return res

    def _succeeded(self, res):
        self._have_winner = True
        self._first_success = res
        for d in list(self._remaining):
            d.cancel()

    def _failed(self, f):
        if self._first_failure is None:
            self._first_failure = f

    def _maybe_done(self, _):
        if self._remaining:
            return
        if self._fired:
            return
        self._fired = True
        if self._have_winner:
            self._winner_d.callback(self._first_success)
        else:
            self._winner_d.errback(self._first_failure)

def there_can_be_only_one(contenders):
    return _ThereCanBeOnlyOne(contenders).run()

class Common:
    RELAY_DELAY = 2.0
    TRANSIT_KEY_LENGTH = SecretBox.KEY_SIZE

    def __init__(self, transit_relay, no_listen=False, tor_manager=None,
                 reactor=reactor, timing=None):
        self._side = bytes_to_hexstr(os.urandom(8)) # unicode
        if transit_relay:
            if not isinstance(transit_relay, type(u"")):
                raise InternalError
            # TODO: allow multiple hints for a single relay
            relay_hint = parse_hint_argv(transit_relay)
            relay = RelayV1Hint(hints=(relay_hint,))
            self._transit_relays = [relay]
        else:
            self._transit_relays = []
        self._their_direct_hints = [] # hintobjs
        self._our_relay_hints = set(self._transit_relays)
        self._tor_manager = tor_manager
        self._transit_key = None
        self._no_listen = no_listen
        self._waiting_for_transit_key = []
        self._listener = None
        self._winner = None
        self._reactor = reactor
        self._timing = timing or DebugTiming()
        self._timing.add("transit")

    def _build_listener(self):
        if self._no_listen or self._tor_manager:
            return ([], None)
        portnum = allocate_tcp_port()
        addresses = ipaddrs.find_addresses()
        non_loopback_addresses = [a for a in addresses if a != "127.0.0.1"]
        if non_loopback_addresses:
            # some test hosts, including the appveyor VMs, *only* have
            # 127.0.0.1, and the tests will hang badly if we remove it.
            addresses = non_loopback_addresses
        direct_hints = [DirectTCPV1Hint(six.u(addr), portnum, 0.0)
                        for addr in addresses]
        ep = endpoints.serverFromString(reactor, "tcp:%d" % portnum)
        return direct_hints, ep

    def get_connection_abilities(self):
        return [{u"type": u"direct-tcp-v1"},
                {u"type": u"relay-v1"},
                ]

    @inlineCallbacks
    def get_connection_hints(self):
        hints = []
        direct_hints = yield self._get_direct_hints()
        for dh in direct_hints:
            hints.append({u"type": u"direct-tcp-v1",
                          u"priority": dh.priority,
                          u"hostname": dh.hostname,
                          u"port": dh.port, # integer
                          })
        for relay in self._transit_relays:
            rhint = {u"type": u"relay-v1", u"hints": []}
            for rh in relay.hints:
                rhint[u"hints"].append({u"type": u"direct-tcp-v1",
                                        u"priority": rh.priority,
                                        u"hostname": rh.hostname,
                                        u"port": rh.port})
            hints.append(rhint)
        returnValue(hints)

    def _get_direct_hints(self):
        if self._listener:
            return defer.succeed(self._my_direct_hints)
        # there is a slight race here: if someone calls get_direct_hints() a
        # second time, before the listener has actually started listening,
        # then they'll get a Deferred that fires (with the hints) before the
        # listener starts listening. But most applications won't call this
        # multiple times, and the race is between 1: the parent Wormhole
        # protocol getting the connection hints to the other end, and 2: the
        # listener being ready for connections, and I'm confident that the
        # listener will win.
        self._my_direct_hints, self._listener = self._build_listener()

        if self._listener is None: # don't listen
            self._listener_d = None
            return defer.succeed(self._my_direct_hints) # empty

        # Start the server, so it will be running by the time anyone tries to
        # connect to the direct hints we return.
        f = InboundConnectionFactory(self)
        self._listener_f = f # for tests # XX move to __init__ ?
        self._listener_d = f.whenDone()
        d = self._listener.listen(f)
        def _listening(lp):
            # lp is an IListeningPort
            #self._listener_port = lp # for tests
            def _stop_listening(res):
                lp.stopListening()
                return res
            self._listener_d.addBoth(_stop_listening)
            return self._my_direct_hints
        d.addCallback(_listening)
        return d

    def _stop_listening(self):
        # this is for unit tests. The usual control flow (via connect())
        # wires the listener's Deferred into a there_can_be_only_one(), which
        # eats the errback. If we don't ever call connect(), we must catch it
        # ourselves.
        self._listener_d.addErrback(lambda f: None)
        self._listener_d.cancel()

    def _parse_tcp_v1_hint(self, hint): # hint_struct -> hint_obj
        hint_type = hint.get(u"type", u"")
        if hint_type not in [u"direct-tcp-v1", u"tor-tcp-v1"]:
            log.msg("unknown hint type: %r" % (hint,))
            return None
        if not(u"hostname" in hint
               and isinstance(hint[u"hostname"], type(u""))):
            log.msg("invalid hostname in hint: %r" % (hint,))
            return None
        if not(u"port" in hint and isinstance(hint[u"port"], int)):
            log.msg("invalid port in hint: %r" % (hint,))
            return None
        priority = hint.get(u"priority", 0.0)
        if hint_type == u"direct-tcp-v1":
            return DirectTCPV1Hint(hint[u"hostname"], hint[u"port"], priority)
        else:
            return TorTCPV1Hint(hint[u"hostname"], hint[u"port"], priority)

    def add_connection_hints(self, hints):
        for h in hints: # hint structs
            hint_type = h.get(u"type", u"")
            if hint_type in [u"direct-tcp-v1", u"tor-tcp-v1"]:
                dh = self._parse_tcp_v1_hint(h)
                if dh:
                    self._their_direct_hints.append(dh) # hint_obj
            elif hint_type == u"relay-v1":
                # TODO: each relay-v1 clause describes a different relay,
                # with a set of equally-valid ways to connect to it. Treat
                # them as separate relays, instead of merging them all
                # together like this.
                relay_hints = []
                for rhs in h.get(u"hints", []):
                    h = self._parse_tcp_v1_hint(rhs)
                    if h:
                        relay_hints.append(h)
                if relay_hints:
                    rh = RelayV1Hint(hints=tuple(sorted(relay_hints)))
                    self._our_relay_hints.add(rh)
            else:
                log.msg("unknown hint type: %r" % (h,))

    def _send_this(self):
        assert self._transit_key
        if self.is_sender:
            return build_sender_handshake(self._transit_key)
        else:
            return build_receiver_handshake(self._transit_key)

    def _expect_this(self):
        assert self._transit_key
        if self.is_sender:
            return build_receiver_handshake(self._transit_key)
        else:
            return build_sender_handshake(self._transit_key)# + b"go\n"

    def _sender_record_key(self):
        assert self._transit_key
        if self.is_sender:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_sender_key")
        else:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_receiver_key")

    def _receiver_record_key(self):
        assert self._transit_key
        if self.is_sender:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_receiver_key")
        else:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_sender_key")

    def set_transit_key(self, key):
        assert isinstance(key, type(b"")), type(key)
        # We use pubsub to protect against the race where the sender knows
        # the hints and the key, and connects to the receiver's transit
        # socket before the receiver gets the relay message (and thus the
        # key).
        self._transit_key = key
        waiters = self._waiting_for_transit_key
        del self._waiting_for_transit_key
        for d in waiters:
            # We don't need eventual-send here. It's safer in general, but
            # set_transit_key() is only called once, and _get_transit_key()
            # won't touch the subscribers list once the key is set.
            d.callback(key)

    def _get_transit_key(self):
        if self._transit_key:
            return defer.succeed(self._transit_key)
        d = defer.Deferred()
        self._waiting_for_transit_key.append(d)
        return d

    @inlineCallbacks
    def connect(self):
        with self._timing.add("transit connect"):
            yield self._get_transit_key()
            # we want to have the transit key before starting any outbound
            # connections, so those connections will know what to say when
            # they connect
            winner = yield self._connect()
        returnValue(winner)

    def _connect(self):
        # It might be nice to wire this so that a failure in the direct hints
        # causes the relay hints to be used right away (fast failover). But
        # none of our current use cases would take advantage of that: if we
        # have any viable direct hints, then they're either going to succeed
        # quickly or hang for a long time.
        contenders = []
        if self._listener_d:
            contenders.append(self._listener_d)
        relay_delay = 0

        for hint_obj in self._their_direct_hints:
            # Check the hint type to see if we can support it (e.g. skip
            # onion hints on a non-Tor client). Do not increase relay_delay
            # unless we have at least one viable hint.
            ep = self._endpoint_from_hint_obj(hint_obj)
            if not ep:
                continue
            description = "->%s" % describe_hint_obj(hint_obj)
            d = self._start_connector(ep, description)
            contenders.append(d)
            relay_delay = self.RELAY_DELAY

        # Start trying the relays a few seconds after we start to try the
        # direct hints. The idea is to prefer direct connections, but not be
        # afraid of using a relay when we have direct hints that don't
        # resolve quickly. Many direct hints will be to unused local-network
        # IP addresses, which won't answer, and would take the full TCP
        # timeout (30s or more) to fail.

        prioritized_relays = {}
        for rh in self._our_relay_hints:
            for hint_obj in rh.hints:
                priority = hint_obj.priority
                if priority not in prioritized_relays:
                    prioritized_relays[priority] = set()
                prioritized_relays[priority].add(hint_obj)

        for priority in sorted(prioritized_relays, reverse=True):
            for hint_obj in prioritized_relays[priority]:
                ep = self._endpoint_from_hint_obj(hint_obj)
                if not ep:
                    continue
                description = "->relay:%s" % describe_hint_obj(hint_obj)
                d = task.deferLater(self._reactor, relay_delay,
                                    self._start_connector, ep, description,
                                    is_relay=True)
                contenders.append(d)
            relay_delay += self.RELAY_DELAY

        if not contenders:
            raise TransitError("No contenders for connection")

        winner = there_can_be_only_one(contenders)
        return self._not_forever(2*TIMEOUT, winner)

    def _not_forever(self, timeout, d):
        """If the timer fires first, cancel the deferred. If the deferred fires
        first, cancel the timer."""
        t = self._reactor.callLater(timeout, d.cancel)
        def _done(res):
            if t.active():
                t.cancel()
            return res
        d.addBoth(_done)
        return d

    def _build_relay_handshake(self):
        return build_sided_relay_handshake(self._transit_key, self._side)

    def _start_connector(self, ep, description, is_relay=False):
        relay_handshake = None
        if is_relay:
            assert self._transit_key
            relay_handshake = self._build_relay_handshake()
        f = OutboundConnectionFactory(self, relay_handshake, description)
        d = ep.connect(f)
        # fires with protocol, or ConnectError
        d.addCallback(lambda p: p.startNegotiation())
        return d

    def _endpoint_from_hint_obj(self, hint):
        if self._tor_manager:
            if isinstance(hint, (DirectTCPV1Hint, TorTCPV1Hint)):
                # our TorManager will return None for non-public IPv4
                # addresses and any IPv6 address
                return self._tor_manager.get_endpoint_for(hint.hostname,
                                                          hint.port)
            return None
        if isinstance(hint, DirectTCPV1Hint):
            return endpoints.HostnameEndpoint(self._reactor,
                                              hint.hostname, hint.port)
        return None

    def connection_ready(self, p):
        # inbound/outbound Connection protocols call this when they finish
        # negotiation. The first one wins and gets a "go". Any subsequent
        # ones lose and get a "nevermind" before being closed.

        if not self.is_sender:
            return "wait-for-decision"

        if self._winner:
            # we already have a winner, so this one loses
            return "nevermind"
        # this one wins!
        self._winner = p
        return "go"

class TransitSender(Common):
    is_sender = True

class TransitReceiver(Common):
    is_sender = False


# based on twisted.protocols.ftp.FileConsumer, but don't close the filehandle
# when done, and add a progress function that gets called with the length of
# each write, and a hasher function that gets called with the data.

@implementer(interfaces.IConsumer)
class FileConsumer:
    def __init__(self, f, progress=None, hasher=None):
        self._f = f
        self._progress = progress
        self._hasher = hasher
        self._producer = None

    def registerProducer(self, producer, streaming):
        assert not self._producer
        self._producer = producer
        assert streaming

    def write(self, bytes):
        self._f.write(bytes)
        if self._progress:
            self._progress(len(bytes))
        if self._hasher:
            self._hasher(bytes)

    def unregisterProducer(self):
        assert self._producer
        self._producer = None

# the TransitSender/Receiver.connect() yields a Connection, on which you can
# do send_record(), but what should the receive API be? set a callback for
# inbound records? get a Deferred for the next record? The producer/consumer
# API is enough for file transfer, but what would other applications want?

# how should the Listener be managed? we want to shut it down when the
# connect() Deferred is cancelled, as well as terminating any negotiations in
# progress.
#
# the factory should return/manage a deferred, which fires iff an inbound
# connection completes negotiation successfully, can be cancelled (which
# stops the listener and drops all pending connections), but will never
# timeout, and only errbacks if cancelled.

# write unit test for _ThereCanBeOnlyOne

# check start/finish time-gathering instrumentation

# relay URLs are probably mishandled: both sides probably send their URL,
# then connect to the *other* side's URL, when they really should connect to
# both their own and the other side's. The current implementation probably
# only works if the two URLs are the same.
