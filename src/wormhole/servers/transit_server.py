from __future__ import print_function
import re, time
from twisted.python import log
from twisted.internet import protocol
from twisted.application import service

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
DAY = 24*HOUR
MB = 1000*1000

def round_to(size, coarseness):
    return int(coarseness*(1+int((size-1)/coarseness)))

def blur_size(size):
    if size == 0:
        return 0
    if size < 1e6:
        return round_to(size, 10e3)
    if size < 1e9:
        return round_to(size, 1e6)
    return round_to(size, 100e6)

class TransitConnection(protocol.Protocol):
    def __init__(self):
        self._got_token = False
        self._token_buffer = b""
        self._sent_ok = False
        self._buddy = None
        self._had_buddy = False
        self._total_sent = 0

    def describeToken(self):
        if self._got_token:
            return self._got_token[:16].decode("ascii")
        return "-"

    def connectionMade(self):
        self._started = time.time()

    def dataReceived(self, data):
        if self._sent_ok:
            # We are an IPushProducer to our buddy's IConsumer, so they'll
            # throttle us (by calling pauseProducing()) when their outbound
            # buffer is full (e.g. when their downstream pipe is full). In
            # practice, this buffers about 10MB per connection, after which
            # point the sender will only transmit data as fast as the
            # receiver can handle it.
            self._total_sent += len(data)
            self._buddy.transport.write(data)
            return

        if self._got_token: # but not yet sent_ok
            self.transport.write(b"impatient\n")
            log.msg("transit impatience failure")
            return self.disconnect() # impatience yields failure

        # else this should be (part of) the token
        self._token_buffer += data
        buf = self._token_buffer
        wanted = len("please relay \n")+32*2
        if len(buf) < wanted-1 and "\n" in buf:
            self.transport.write(b"bad handshake\n")
            log.msg("transit handshake early failure")
            return self.disconnect()
        if len(buf) < wanted:
            return
        if len(buf) > wanted:
            self.transport.write(b"impatient\n")
            log.msg("transit impatience failure")
            return self.disconnect() # impatience yields failure
        mo = re.search(br"^please relay (\w{64})\n", buf, re.M)
        if not mo:
            self.transport.write(b"bad handshake\n")
            log.msg("transit handshake failure")
            return self.disconnect() # incorrectness yields failure
        token = mo.group(1)

        self._got_token = token
        self.factory.connection_got_token(token, self)

    def buddy_connected(self, them):
        self._buddy = them
        self._had_buddy = True
        self.transport.write(b"ok\n")
        self._sent_ok = True
        # Connect the two as a producer/consumer pair. We use streaming=True,
        # so this expects the IPushProducer interface, and uses
        # pauseProducing() to throttle, and resumeProducing() to unthrottle.
        self._buddy.transport.registerProducer(self.transport, True)
        # The Transit object calls buddy_connected() on both protocols, so
        # there will be two producer/consumer pairs.

    def buddy_disconnected(self):
        log.msg("buddy_disconnected %s" % self.describeToken())
        self._buddy = None
        self.transport.loseConnection()

    def connectionLost(self, reason):
        if self._buddy:
            self._buddy.buddy_disconnected()
        self.factory.transitFinished(self, self._got_token,
                                     self.describeToken())

        # Record usage. There are four cases:
        # * 1: we connected, never had a buddy
        # * 2: we connected first, we disconnect before the buddy
        # * 3: we connected first, buddy disconnects first
        # * 4: buddy connected first, we disconnect before buddy
        # * 5: buddy connected first, buddy disconnects first

        # whoever disconnects first gets to write the usage record (1,2,4)

        finished = time.time()
        if not self._had_buddy: # 1
            total_time = finished - self._started
            self.factory.recordUsage(self._started, u"lonely", 0,
                                     total_time, None)
        if self._had_buddy and self._buddy: # 2,4
            total_bytes = self._total_sent + self._buddy._total_sent
            starts = [self._started, self._buddy._started]
            total_time = finished - min(starts)
            waiting_time = max(starts) - min(starts)
            self.factory.recordUsage(self._started, u"happy", total_bytes,
                                     total_time, waiting_time)

    def disconnect(self):
        self.transport.loseConnection()
        self.factory.transitFailed(self)
        finished = time.time()
        total_time = finished - self._started
        self.factory.recordUsage(self._started, u"errory", 0,
                                 total_time, None)

class Transit(protocol.ServerFactory, service.MultiService):
    # I manage pairs of simultaneous connections to a secondary TCP port,
    # both forwarded to the other. Clients must begin each connection with
    # "please relay TOKEN\n". I will send "ok\n" when the matching connection
    # is established, or disconnect if no matching connection is made within
    # MAX_WAIT_TIME seconds. I will disconnect if you send data before the
    # "ok\n". All data you get after the "ok\n" will be from the other side.
    # You will not receive "ok\n" until the other side has also connected and
    # submitted a matching token. The token is the same for each side.

    # In addition, the connections will be dropped after MAXLENGTH bytes have
    # been sent by either side, or MAXTIME seconds have elapsed after the
    # matching connections were established. A future API will reveal these
    # limits to clients instead of causing mysterious spontaneous failures.

    # These relay connections are not half-closeable (unlike full TCP
    # connections, applications will not receive any data after half-closing
    # their outgoing side). Applications must negotiate shutdown with their
    # peer and not close the connection until all data has finished
    # transferring in both directions. Applications which only need to send
    # data in one direction can use close() as usual.

    MAX_WAIT_TIME = 30*SECONDS
    MAXLENGTH = 10*MB
    MAXTIME = 60*SECONDS
    protocol = TransitConnection

    def __init__(self, db, blur_usage):
        service.MultiService.__init__(self)
        self._db = db
        self._blur_usage = blur_usage
        self._pending_requests = {} # token -> TransitConnection
        self._active_connections = set() # TransitConnection

    def connection_got_token(self, token, p):
        if token in self._pending_requests:
            log.msg("transit relay 2: %s" % p.describeToken())
            buddy = self._pending_requests.pop(token)
            self._active_connections.add(p)
            self._active_connections.add(buddy)
            p.buddy_connected(buddy)
            buddy.buddy_connected(p)
        else:
            self._pending_requests[token] = p
            log.msg("transit relay 1: %s" % p.describeToken())
            # TODO: timer

    def recordUsage(self, started, result, total_bytes,
                    total_time, waiting_time):
        log.msg("Transit.recordUsage (%dB)" % total_bytes)
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
            total_bytes = blur_size(total_bytes)
        self._db.execute("INSERT INTO `usage`"
                         " (`type`, `started`, `result`, `total_bytes`,"
                         "  `total_time`, `waiting_time`)"
                         " VALUES (?,?,?,?, ?,?)",
                         (u"transit", started, result, total_bytes,
                          total_time, waiting_time))
        self._db.commit()

    def transitFinished(self, p, token, description):
        for token,tc in self._pending_requests.items():
            if tc is p:
                del self._pending_requests[token]
                break
        log.msg("transitFinished %s" % (description,))
        self._active_connections.discard(p)

    def transitFailed(self, p):
        log.msg("transitFailed %r" % p)
        pass
