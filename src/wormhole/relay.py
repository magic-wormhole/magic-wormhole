import os, re, json, binascii
from collections import defaultdict
from twisted.python import log
from twisted.internet import protocol
from twisted.application import strports, service
from twisted.web import server, static, resource, http

SECONDS = 1.0
MB = 1000*1000

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


class Relay(resource.Resource):
    def __init__(self):
        resource.Resource.__init__(self)
        self.channels = {}
        self.next_channel = 1

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

class Transit(resource.Resource, protocol.ServerFactory, service.MultiService):
    # Transit manages two simultaneous connections to a secondary TCP port,
    # both forwarded to the other. Transit will allocate you a token when the
    # ports are free, and will inform you of the MAXLENGTH and MAXTIME
    # limits. Connect to the port, send "TOKEN\n", receive "ok\n", and all
    # subsequent data you send will be delivered to the other side. All data
    # you get after the "ok" will be from the other side. You will not
    # receive "ok" until the other side has also connected and submitted a
    # valid token. The token is different for each side. The connections will
    # be dropped after MAXLENGTH bytes have been sent by either side, or
    # MAXTIME seconds after the token is issued, whichever is reached first.

    # These relay connections are not half-closeable (unlike full TCP
    # connections, applications will not receive any data after half-closing
    # their outgoing side). Applications must negotiate shutdown with their
    # peer and not close the connection until all data has finished
    # transferring in both directions. Applications which only need to send
    # data in one direction can use close as usual.

    MAXLENGTH = 10*MB
    MAXTIME = 60*SECONDS

    def __init__(self):
        resource.Resource.__init__(self)
        service.MultiService.__init__(self)
        self.pending_requests = []
        self.active_token = None
        self.active_connection = None
        self.active_timer = None

    def make_token(self):
        return binascii.hexlify(os.urandom(8))

    def render_POST(self, request):
        if self.active_connection:
            self.pending_requests.append(request)
            return server.NOT_DONE_YET
        self.active_token = self.make_token()
        request.setHeader("content-type", "application/json; charset=utf-8")
        t = service.TimerService(self.MAXTIME, self.timer_expired)
        self.active_timer = t
        t.setServiceParent(self)
        r = { "token": self.active_token,
              "maxlength": self.MAXLENGTH,
              "maxtime": self.MAXTIME,
              }
        return json.dumps(r)+"\n"

    def timer_expired(self):
        self.remove_timer()
        for c in self.active_connections:
            c.STOPSTOP()
        self.active_connections[:] = []
        self.active_token = None

    def remove_timer(self):
        self.active_timer.disownServiceParent()
        self.active_timer = None

    # ServerFactory methods, which manage the two TransitConnection protocols

    def buildProtocol(self, addr):
        p = TransitConnection(self.active_token)
        p.factory = self
        return p

    def connection_got_token(self, p):
        pass
    def transitFinished(self, p):
        pass
    def transitFailed(self):
        pass

# after getting a token, both transit clients connect to one of these

class TransitConnection(protocol.Protocol):
    def __init__(self, expected_token):
        self.expected_token = expected_token
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
            return self.disconnect() # impatience yields failure
        # else this should be (part of) the token
        self.token_buffer += data
        if b"\n" not in self.token_buffer:
            return
        lines = self.token_buffer.split(b"\n")
        if len(lines) > 1:
            return self.disconnect() # impatience yields failure
        token = lines[0]
        if token != self.expected_token:
            return self.disconnect() # incorrectness yields failure
        self.got_token = True
        self.factory.connection_got_token(self)

    def buddy_connected(self, them):
        self.buddy = them
        self.transport.write(b"ok\n")
        self.sent_ok = True

    def buddy_disconnected(self):
        self.buddy = None
        self.transport.loseConnection()
        self.factory.transitFinished(self)

    def connectionLost(self, reason):
        if self.buddy:
            self.buddy.buddy_disconnected()

    def disconnect(self):
        self.transport.loseConnection()
        self.factory.transitFailed()


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
        self.relay = Relay() # for tests
        self.root.putChild("relay", self.relay)
        self.transit = Transit()
        self.root.putChild("transit", self.transit)
        self.transit.setServiceParent(self) # for the timer
        self.transport_service = strports.service(transitport, self.transit)
        self.transport_service.setServiceParent(self)

application = service.Application("foo")
RelayServer("tcp:8009", "tcp:8010").setServiceParent(application)
