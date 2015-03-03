from __future__ import print_function
import re, json, time
from collections import defaultdict
from twisted.python import log
from twisted.internet import protocol
from twisted.application import strports, service, internet
from twisted.web import server, static, resource, http

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
MB = 1000*1000

CHANNEL_EXPIRATION_TIME = 1*HOUR

class Channel(resource.Resource):
    isLeaf = True

    # POST /CHANNEL-ID/SIDE/pake/post  {message: STR} -> {messages: [STR..]}
    # POST /CHANNEL-ID/SIDE/pake/poll                 -> {messages: [STR..]}
    # POST /CHANNEL-ID/SIDE/data/post  {message: STR} -> {messages: [STR..]}
    # POST /CHANNEL-ID/SIDE/data/poll                 -> {messages: [STR..]}
    # POST /CHANNEL-ID/SIDE/deallocate                -> waiting | deleted

    def __init__(self, channel_id, relay):
        resource.Resource.__init__(self)
        self.channel_id = channel_id
        self.relay = relay
        self.expire_at = time.time() + CHANNEL_EXPIRATION_TIME
        self.sides = set()
        self.messages = {"pake": defaultdict(list), # side -> [strings]
                         "data": defaultdict(list), # side -> [strings]
                         }

    def render_POST(self, request):
        side = request.postpath[0]
        self.sides.add(side)
        which = request.postpath[1]

        if which == "deallocate":
            self.sides.remove(side)
            if self.sides:
                return "waiting\n"
            self.relay.free_child(self.channel_id)
            return "deleted\n"
        elif which in ("pake", "data"):
            all_messages = self.messages[which]
            messages = all_messages[side]
            other_messages = []
            for other_side, other_msgs in all_messages.items():
                if other_side != side:
                    other_messages.extend(other_msgs)
        else:
            request.setResponseCode(http.BAD_REQUEST)
            return "bad command, want 'pake' or 'data' or 'deallocate'\n"

        verb = request.postpath[2]
        if verb not in ("post", "poll"):
            request.setResponseCode(http.BAD_REQUEST)
            return "bad verb, want 'post' or 'poll'\n"

        if verb == "post":
            data = json.load(request.content)
            messages.append(data["message"])

        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"messages": other_messages})+"\n"

class Allocated(resource.Resource):
    def __init__(self, channel_id):
        resource.Resource.__init__(self)
        self.channel_id = channel_id
    def render_POST(self, request):
        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"channel-id": self.channel_id})+"\n"

class ChannelList(resource.Resource):
    def __init__(self, channel_ids):
        resource.Resource.__init__(self)
        self.channel_ids = channel_ids
    def render_GET(self, request):
        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"channel-ids": self.channel_ids})+"\n"

class Relay(resource.Resource):
    def __init__(self):
        resource.Resource.__init__(self)
        self.channels = {}
        self.next_channel = 1

    def prune_old_channels(self):
        now = time.time()
        for channel_id in list(self.channels):
            c = self.channels[channel_id]
            if c.expire_at < now:
                log.msg("expiring %d" % channel_id)
                self.free_child(channel_id)

    def getChild(self, path, request):
        if path == "allocate":
            # be more clever later. Rotate through 1-99 unless they're all
            # full, then rotate through 1-999, etc.
            channel_id = self.next_channel
            self.next_channel += 1
            self.channels[channel_id] = Channel(channel_id, self)
            log.msg("allocated %d, now have %d channels" %
                    (channel_id, len(self.channels)))
            return Allocated(channel_id)
        if path == "list":
            channel_ids = sorted(self.channels.keys())
            return ChannelList(channel_ids)
        if not re.search(r'^\d+$', path):
            return resource.ErrorPage(http.BAD_REQUEST,
                                      "invalid channel id",
                                      "invalid channel id")
        channel_id = int(path)
        if not channel_id in self.channels:
            return resource.ErrorPage(http.NOT_FOUND,
                                      "invalid channel id",
                                      "invalid channel id")
        return self.channels[channel_id]

    def free_child(self, channel_id):
        self.channels.pop(channel_id)
        log.msg("freed %d, now have %d channels" %
                (channel_id, len(self.channels)))

class TransitConnection(protocol.Protocol):
    def __init__(self):
        self.got_token = False
        self.token_buffer = b""
        self.sent_ok = False
        self.buddy = None

    def dataReceived(self, data):
        if self.sent_ok:
            # TODO: connect as producer/consumer
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
        self.factory.transitFinished(self)

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
    def transitFinished(self, p):
        print("transitFinished %r" % p)
        for token,tc in self.pending_requests.items():
            if tc is p:
                del self.pending_requests[token]
                break
        self.active_connections.discard(p)

    def transitFailed(self, p):
        print("transitFailed %r" % p)
        pass


class Root(resource.Resource):
    # child_FOO is a nevow thing, not a twisted.web.resource thing
    def __init__(self):
        resource.Resource.__init__(self)
        self.putChild("", static.Data("Wormhole Relay\n", "text/plain"))

class RelayServer(service.MultiService):
    def __init__(self, relayport, transitport):
        service.MultiService.__init__(self)
        self.root = Root()
        site = server.Site(self.root)
        self.relayport_service = strports.service(relayport, site)
        self.relayport_service.setServiceParent(self)
        self.relay = Relay() # accessible from tests
        self.root.putChild("relay", self.relay)
        t = internet.TimerService(5*MINUTE, self.relay.prune_old_channels)
        t.setServiceParent(self)
        self.transit = Transit()
        self.transit.setServiceParent(self) # for the timer
        self.transport_service = strports.service(transitport, self.transit)
        self.transport_service.setServiceParent(self)

application = service.Application("foo")
RelayServer("tcp:8009", "tcp:8010").setServiceParent(application)
