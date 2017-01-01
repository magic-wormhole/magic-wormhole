from __future__ import print_function, unicode_literals
import re, time, collections
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
        self._got_side = False
        self._token_buffer = b""
        self._sent_ok = False
        self._buddy = None
        self._had_buddy = False
        self._total_sent = 0

    def describeToken(self):
        d = "-"
        if self._got_token:
            d = self._got_token[:16].decode("ascii")
        if self._got_side:
            d += "-" + self._got_side.decode("ascii")
        else:
            d += "-<unsided>"
        return d

    def connectionMade(self):
        self._started = time.time()
        self._log_requests = self.factory._log_requests

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
            if self._log_requests:
                log.msg("transit impatience failure")
            return self.disconnect() # impatience yields failure

        # else this should be (part of) the token
        self._token_buffer += data
        buf = self._token_buffer

        # old: "please relay {64}\n"
        # new: "please relay {64} for side {16}\n"
        (old, handshake_len, token) = self._check_old_handshake(buf)
        assert old in ("yes", "waiting", "no")
        if old == "yes":
            # remember they aren't supposed to send anything past their
            # handshake until we've said go
            if len(buf) > handshake_len:
                self.transport.write(b"impatient\n")
                if self._log_requests:
                    log.msg("transit impatience failure")
                return self.disconnect() # impatience yields failure
            return self._got_handshake(token, None)
        (new, handshake_len, token, side) = self._check_new_handshake(buf)
        assert new in ("yes", "waiting", "no")
        if new == "yes":
            if len(buf) > handshake_len:
                self.transport.write(b"impatient\n")
                if self._log_requests:
                    log.msg("transit impatience failure")
                return self.disconnect() # impatience yields failure
            return self._got_handshake(token, side)
        if (old == "no" and new == "no"):
            self.transport.write(b"bad handshake\n")
            if self._log_requests:
                log.msg("transit handshake failure")
            return self.disconnect() # incorrectness yields failure
        # else we'll keep waiting

    def _check_old_handshake(self, buf):
        # old: "please relay {64}\n"
        # return ("yes", handshake, token) if buf contains an old-style handshake
        # return ("waiting", None, None) if it might eventually contain one
        # return ("no", None, None) if it could never contain one
        wanted = len("please relay \n")+32*2
        if len(buf) < wanted-1 and b"\n" in buf:
            return ("no", None, None)
        if len(buf) < wanted:
            return ("waiting", None, None)

        mo = re.search(br"^please relay (\w{64})\n", buf, re.M)
        if mo:
            token = mo.group(1)
            return ("yes", wanted, token)
        return ("no", None, None)

    def _check_new_handshake(self, buf):
        # new: "please relay {64} for side {16}\n"
        wanted = len("please relay  for side \n")+32*2+8*2
        if len(buf) < wanted-1 and b"\n" in buf:
            return ("no", None, None, None)
        if len(buf) < wanted:
            return ("waiting", None, None, None)

        mo = re.search(br"^please relay (\w{64}) for side (\w{16})\n", buf, re.M)
        if mo:
            token = mo.group(1)
            side = mo.group(2)
            return ("yes", wanted, token, side)
        return ("no", None, None, None)

    def _got_handshake(self, token, side):
        self._got_token = token
        self._got_side = side
        self.factory.connection_got_token(token, side, self)

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
        if self._log_requests:
            log.msg("buddy_disconnected %s" % self.describeToken())
        self._buddy = None
        self.transport.loseConnection()

    def connectionLost(self, reason):
        if self._buddy:
            self._buddy.buddy_disconnected()
        self.factory.transitFinished(self, self._got_token, self._got_side,
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
            self.factory.recordUsage(self._started, "lonely", 0,
                                     total_time, None)
        if self._had_buddy and self._buddy: # 2,4
            total_bytes = self._total_sent + self._buddy._total_sent
            starts = [self._started, self._buddy._started]
            total_time = finished - min(starts)
            waiting_time = max(starts) - min(starts)
            self.factory.recordUsage(self._started, "happy", total_bytes,
                                     total_time, waiting_time)

    def disconnect(self):
        self.transport.loseConnection()
        self.factory.transitFailed(self)
        finished = time.time()
        total_time = finished - self._started
        self.factory.recordUsage(self._started, "errory", 0,
                                 total_time, None)

class Transit(protocol.ServerFactory, service.MultiService):
    # I manage pairs of simultaneous connections to a secondary TCP port,
    # both forwarded to the other. Clients must begin each connection with
    # "please relay TOKEN for SIDE\n" (or a legacy form without the "for
    # SIDE"). Two connections match if they use the same TOKEN and have
    # different SIDEs (the redundant connections are dropped when a match is
    # made). Legacy connections match any with the same TOKEN, ignoring SIDE
    # (so two legacy connections will match each other).

    # I will send "ok\n" when the matching connection is established, or
    # disconnect if no matching connection is made within MAX_WAIT_TIME
    # seconds. I will disconnect if you send data before the "ok\n". All data
    # you get after the "ok\n" will be from the other side. You will not
    # receive "ok\n" until the other side has also connected and submitted a
    # matching token (and differing SIDE).

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
        self._log_requests = blur_usage is None
        self._pending_requests = {} # token -> set((side, TransitConnection))
        self._active_connections = set() # TransitConnection
        self._counts = collections.defaultdict(int)
        self._count_bytes = 0

    def connection_got_token(self, token, new_side, new_tc):
        if token not in self._pending_requests:
            self._pending_requests[token] = set()
        potentials = self._pending_requests[token]
        for old in potentials:
            (old_side, old_tc) = old
            if ((old_side is None)
                or (new_side is None)
                or (old_side != new_side)):
                # we found a match
                if self._log_requests:
                    log.msg("transit relay 2: %s" % new_tc.describeToken())

                # drop and stop tracking the rest
                potentials.remove(old)
                for (_, leftover_tc) in potentials:
                    leftover_tc.disconnect() # TODO: not "errory"?
                self._pending_requests.pop(token)

                # glue the two ends together
                self._active_connections.add(new_tc)
                self._active_connections.add(old_tc)
                new_tc.buddy_connected(old_tc)
                old_tc.buddy_connected(new_tc)
                return
        if self._log_requests:
            log.msg("transit relay 1: %s" % new_tc.describeToken())
        potentials.add((new_side, new_tc))
        # TODO: timer

    def recordUsage(self, started, result, total_bytes,
                    total_time, waiting_time):
        if self._log_requests:
            log.msg("Transit.recordUsage (%dB)" % total_bytes)
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
            total_bytes = blur_size(total_bytes)
        self._db.execute("INSERT INTO `transit_usage`"
                         " (`started`, `total_time`, `waiting_time`,"
                         "  `total_bytes`, `result`)"
                         " VALUES (?,?,?, ?,?)",
                         (started, total_time, waiting_time,
                          total_bytes, result))
        self._db.commit()
        self._counts[result] += 1
        self._count_bytes += total_bytes

    def transitFinished(self, tc, token, side, description):
        if token in self._pending_requests:
            side_tc = (side, tc)
            if side_tc in self._pending_requests[token]:
                self._pending_requests[token].remove(side_tc)
            if not self._pending_requests[token]: # set is now empty
                del self._pending_requests[token]
        if self._log_requests:
            log.msg("transitFinished %s" % (description,))
        self._active_connections.discard(tc)

    def transitFailed(self, p):
        if self._log_requests:
            log.msg("transitFailed %r" % p)
        pass

    def get_stats(self):
        stats = {}
        def q(query, values=()):
            row = self._db.execute(query, values).fetchone()
            return list(row.values())[0]

        # current status: expected to be zero most of the time
        c = stats["active"] = {}
        c["connected"] = len(self._active_connections) / 2
        c["waiting"] = len(self._pending_requests)

        # usage since last reboot
        rb = stats["since_reboot"] = {}
        rb["bytes"] = self._count_bytes
        rb["total"] = sum(self._counts.values(), 0)
        rbm = rb["moods"] = {}
        for result, count in self._counts.items():
            rbm[result] = count

        # historical usage (all-time)
        u = stats["all_time"] = {}
        u["total"] = q("SELECT COUNT() FROM `transit_usage`")
        u["bytes"] = q("SELECT SUM(`total_bytes`) FROM `transit_usage`") or 0
        um = u["moods"] = {}
        um["happy"] = q("SELECT COUNT() FROM `transit_usage`"
                        " WHERE `result`='happy'")
        um["lonely"] = q("SELECT COUNT() FROM `transit_usage`"
                         " WHERE `result`='lonely'")
        um["errory"] = q("SELECT COUNT() FROM `transit_usage`"
                         " WHERE `result`='errory'")

        return stats
