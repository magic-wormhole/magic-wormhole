from __future__ import print_function
import re
from twisted.internet import protocol
from twisted.application import service

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
DAY = 24*HOUR
MB = 1000*1000

class TransitConnection(protocol.Protocol):
    def __init__(self):
        self.got_token = False
        self.token_buffer = b""
        self.sent_ok = False
        self.buddy = None
        self.total_sent = 0

    def dataReceived(self, data):
        if self.sent_ok:
            # TODO: connect as producer/consumer
            self.total_sent += len(data)
            self.buddy.transport.write(data)
            return
        if self.got_token: # but not yet sent_ok
            self.transport.write("impatient\n")
            print("transit impatience failure")
            return self.disconnect() # impatience yields failure
        # else this should be (part of) the token
        self.token_buffer += data
        buf = self.token_buffer
        wanted = len("please relay \n")+32*2
        if len(buf) < wanted-1 and "\n" in buf:
            self.transport.write("bad handshake\n")
            print("transit handshake early failure")
            return self.disconnect()
        if len(buf) < wanted:
            return
        if len(buf) > wanted:
            self.transport.write("impatient\n")
            print("transit impatience failure")
            return self.disconnect() # impatience yields failure
        mo = re.search(r"^please relay (\w{64})\n", buf, re.M)
        if not mo:
            self.transport.write("bad handshake\n")
            print("transit handshake failure")
            return self.disconnect() # incorrectness yields failure
        token = mo.group(1)

        self.got_token = True
        self.factory.connection_got_token(token, self)

    def buddy_connected(self, them):
        self.buddy = them
        self.transport.write(b"ok\n")
        self.sent_ok = True
        # TODO: connect as producer/consumer

    def buddy_disconnected(self):
        print("buddy_disconnected %r" % self)
        self.buddy = None
        self.transport.loseConnection()

    def connectionLost(self, reason):
        print("connectionLost %r %s" % (self, reason))
        if self.buddy:
            self.buddy.buddy_disconnected()
        self.factory.transitFinished(self, self.total_sent)

    def disconnect(self):
        self.transport.loseConnection()
        self.factory.transitFailed(self)

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

    def __init__(self):
        service.MultiService.__init__(self)
        self.pending_requests = {} # token -> TransitConnection
        self.active_connections = set() # TransitConnection

    def connection_got_token(self, token, p):
        if token in self.pending_requests:
            print("transit relay 2: %r" % token)
            buddy = self.pending_requests.pop(token)
            self.active_connections.add(p)
            self.active_connections.add(buddy)
            p.buddy_connected(buddy)
            buddy.buddy_connected(p)
        else:
            self.pending_requests[token] = p
            print("transit relay 1: %r" % token)
            # TODO: timer
    def transitFinished(self, p, total_sent):
        print("transitFinished (%dB) %r" % (total_sent, p))
        for token,tc in self.pending_requests.items():
            if tc is p:
                del self.pending_requests[token]
                break
        self.active_connections.discard(p)

    def transitFailed(self, p):
        print("transitFailed %r" % p)
        pass
