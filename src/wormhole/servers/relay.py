from __future__ import print_function
import re, json, time, random
from twisted.python import log
from twisted.internet import protocol
from twisted.application import strports, service, internet
from twisted.web import server, static, resource, http
from .. import __version__

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
MB = 1000*1000

CHANNEL_EXPIRATION_TIME = 1*HOUR

class EventsProtocol:
    def __init__(self, request):
        self.request = request

    def sendComment(self, comment):
        # this is ignored by clients, but can keep the connection open in the
        # face of firewall/NAT timeouts. It also helps unit tests, since
        # apparently twisted.web.client.Agent doesn't consider the connection
        # to be established until it sees the first byte of the reponse body.
        self.request.write(": %s\n\n" % comment)

    def sendEvent(self, data, name=None, id=None, retry=None):
        if name:
            self.request.write("event: %s\n" % name.encode("utf-8"))
            # e.g. if name=foo, then the client web page should do:
            # (new EventSource(url)).addEventListener("foo", handlerfunc)
            # Note that this basically defaults to "message".
            self.request.write("\n")
        if id:
            self.request.write("id: %s\n" % id.encode("utf-8"))
            self.request.write("\n")
        if retry:
            self.request.write("retry: %d\n" % retry) # milliseconds
            self.request.write("\n")
        for line in data.splitlines():
            self.request.write("data: %s\n" % line.encode("utf-8"))
        self.request.write("\n")

    def stop(self):
        self.request.finish()

# note: no versions of IE (including the current IE11) support EventSource

WELCOME = {
    "current_version": __version__,
    # adding .motd will cause all clients to display the message, then keep
    # running normally
    #"motd": "Welcome to the public relay.\nPlease enjoy this service.",
    # adding .error will cause all clients to fail, with this message
    #"error": "This server has been disabled, see URL for details.",
    }

# relay URLs are:
# GET /list                                         -> {channel-ids: [INT..]}
# POST /allocate/SIDE                               -> {channel-id: INT}
#  these return all messages for CHANNEL-ID= and MSGNUM= but SIDE!= :
# POST /CHANNEL-ID/SIDE/post/MSGNUM  {message: STR} -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/poll/MSGNUM                 -> {messages: [STR..]}
# GET  /CHANNEL-ID/SIDE/poll/MSGNUM (eventsource)   -> STR, STR, ..
# POST /CHANNEL-ID/SIDE/deallocate                  -> waiting | deleted

class Channel(resource.Resource):
    isLeaf = True # I handle /CHANNEL-ID/*

    def __init__(self, channel_id, relay):
        resource.Resource.__init__(self)
        self.channel_id = channel_id
        self.relay = relay
        self.expire_at = time.time() + CHANNEL_EXPIRATION_TIME
        self.sides = set()
        self.messages = [] # (side, msgnum, str)
        self.event_channels = set() # (side, msgnum, ep)


    def render_GET(self, request):
        # rest of URL is: SIDE/poll/MSGNUM
        their_side = request.postpath[0]
        if request.postpath[1] != "poll":
            request.setResponseCode(http.BAD_REQUEST, "GET to wrong URL")
            return "GET is only for /SIDE/poll/MSGNUM"
        their_msgnum = request.postpath[2]
        if "text/event-stream" not in (request.getHeader("accept") or ""):
            request.setResponseCode(http.BAD_REQUEST, "Must use EventSource")
            return "Must use EventSource (Content-Type: text/event-stream)"
        request.setHeader("content-type", "text/event-stream")
        ep = EventsProtocol(request)
        ep.sendEvent(json.dumps(WELCOME), name="welcome")
        handle = (their_side, their_msgnum, ep)
        self.event_channels.add(handle)
        request.notifyFinish().addErrback(self._shutdown, handle)
        for (msg_side, msg_msgnum, msg_str) in self.messages:
            self.message_added(msg_side, msg_msgnum, msg_str, channels=[handle])
        return server.NOT_DONE_YET

    def _shutdown(self, _, handle):
        self.event_channels.discard(handle)


    def message_added(self, msg_side, msg_msgnum, msg_str, channels=None):
        if channels is None:
            channels = self.event_channels
        for (their_side, their_msgnum, their_ep) in channels:
            if msg_side != their_side and msg_msgnum == their_msgnum:
                data = json.dumps({ "side": msg_side, "message": msg_str })
                their_ep.sendEvent(data)


    def render_POST(self, request):
        # rest of URL is: SIDE/(MSGNUM|deallocate)/(post|poll)
        side = request.postpath[0]
        self.sides.add(side)
        verb = request.postpath[1]

        if verb == "deallocate":
            self.sides.remove(side)
            if self.sides:
                return "waiting\n"
            self.relay.free_child(self.channel_id)
            return "deleted\n"

        if verb not in ("post", "poll"):
            request.setResponseCode(http.BAD_REQUEST)
            return "bad verb, want 'post' or 'poll'\n"

        msgnum = request.postpath[2]

        other_messages = []
        for (msg_side, msg_msgnum, msg_str) in self.messages:
            if msg_side != side and msg_msgnum == msgnum:
                other_messages.append(msg_str)

        if verb == "post":
            data = json.load(request.content)
            self.messages.append( (side, msgnum, data["message"]) )
            self.message_added(side, msgnum, data["message"])

        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"welcome": WELCOME,
                           "messages": other_messages})+"\n"

class Allocator(resource.Resource):
    isLeaf = True
    def __init__(self, relay):
        resource.Resource.__init__(self)
        self.relay = relay
    def render_POST(self, request):
        side = request.postpath[0]
        channel_id = self.relay.allocate_channel_id()
        self.relay.channels[channel_id] = Channel(channel_id, self.relay)
        log.msg("allocated #%d, now have %d channels" %
                (channel_id, len(self.relay.channels)))
        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"welcome": WELCOME,
                           "channel-id": channel_id})+"\n"

class ChannelList(resource.Resource):
    def __init__(self, channel_ids):
        resource.Resource.__init__(self)
        self.channel_ids = channel_ids
    def render_GET(self, request):
        request.setHeader("content-type", "application/json; charset=utf-8")
        return json.dumps({"welcome": WELCOME,
                           "channel-ids": self.channel_ids})+"\n"

class Relay(resource.Resource):
    def __init__(self):
        resource.Resource.__init__(self)
        self.channels = {}

    def prune_old_channels(self):
        now = time.time()
        for channel_id in list(self.channels):
            c = self.channels[channel_id]
            if c.expire_at < now:
                log.msg("expiring %d" % channel_id)
                self.free_child(channel_id)

    def allocate_channel_id(self):
        for size in range(1,4): # stick to 1-999 for now
            available = set()
            for cid in range(10**(size-1), 10**size):
                if cid not in self.channels:
                    available.add(cid)
            if available:
                return random.choice(list(available))
        # ouch, 999 currently allocated. Try random ones for a while.
        for tries in range(1000):
            cid = random.randrange(1000, 1000*1000)
            if cid not in self.channels:
                return cid
        raise ValueError("unable to find a free channel-id")

    def getChild(self, path, request):
        if path == "allocate":
            return Allocator(self)
        if path == "list":
            channel_ids = sorted(self.channels.keys())
            return ChannelList(channel_ids)
        if not re.search(r'^\d+$', path):
            return resource.ErrorPage(http.BAD_REQUEST,
                                      "invalid channel id",
                                      "invalid channel id")
        channel_id = int(path)
        if not channel_id in self.channels:
            log.msg("claimed #%d, now have %d channels" %
                    (channel_id, len(self.channels)))
            self.channels[channel_id] = Channel(channel_id, self)
        return self.channels[channel_id]

    def free_child(self, channel_id):
        self.channels.pop(channel_id)
        log.msg("freed #%d, now have %d channels" %
                (channel_id, len(self.channels)))

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
        self.root.putChild("wormhole-relay", self.relay)
        t = internet.TimerService(5*MINUTE, self.relay.prune_old_channels)
        t.setServiceParent(self)
        self.transit = Transit()
        self.transit.setServiceParent(self) # for the timer
        self.transport_service = strports.service(transitport, self.transit)
        self.transport_service.setServiceParent(self)

application = service.Application("foo")
RelayServer("tcp:3000", "tcp:3001").setServiceParent(application)
