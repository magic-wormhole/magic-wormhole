import json, time
from twisted.internet import reactor
from twisted.python import log
from autobahn.twisted import websocket

# Each WebSocket connection is bound to one "appid", one "side", and zero or
# more "channelids". The connection's appid and side are set by the "bind"
# message (which must be the first message on the connection). Both must be
# set before any other message (allocate, claim, watch, add, deallocate) will
# be accepted. Short channel IDs can be obtained from the server with an
# "allocate" message. Longer ones can be selected independently by the
# client. Channels are maintained (saved from deletion) by a "claim" message
# (and also incidentally by "allocate"). Channels are deleted when the last
# claim is released with "release".

# All websocket messages are JSON-encoded. The client can send us "inbound"
# messages (marked as "->" below), which may (or may not) provoke immediate
# (or delayed) "outbound" messages (marked as "<-"). There is no guaranteed
# correlation between requests and responses. In this list, "A -> B" means
# that some time after A is received, at least one message of type B will be
# sent out (probably).

# All outbound messages include a "server_tx" key, which is a float (seconds
# since epoch) with the server clock just before the outbound message was
# written to the socket. Unrecognized keys will be ignored.

# connection -> welcome
#  <- {type: "welcome", welcome: {}} # .welcome keys are all optional:
#        current_version: out-of-date clients display a warning
#        motd: all clients display message, then continue normally
#        error: all clients display mesage, then terminate with error
# -> {type: "bind", appid:, side:}
#
# -> {type: "list"} -> channelids
#  <- {type: "channelids", channelids: [int..]}
# -> {type: "allocate"} -> allocated
#  <- {type: "allocated", channelid: int}
# -> {type: "claim", channelid: int}
#
# -> {type: "watch", channelid: int} -> message
#     sends old messages and more in future
#  <- {type: "message", channelid: int, message: {phase:, body:}} # body is hex
# -> {type: "add", channelid: int, phase: str, body: hex} # will send echo
#
# -> {type: "release", channelid: int, mood: str} -> deallocated
#  <- {type: "released", channelid: int, status: waiting|deleted}
#
#  <- {type: "error", error: str, orig: {}} # in response to malformed msgs

# for tests that need to know when a message has been processed:
# -> {type: "ping", ping: int} -> pong (does not require bind/claim)
#  <- {type: "pong", pong: int}

class Error(Exception):
    def __init__(self, explain):
        self._explain = explain

class WebSocketRendezvous(websocket.WebSocketServerProtocol):
    def __init__(self):
        websocket.WebSocketServerProtocol.__init__(self)
        self._app = None
        self._side = None
        self._did_allocate = False # only one allocate() per websocket
        self._channels = {} # channel-id -> Channel (claimed)

    def onConnect(self, request):
        rv = self.factory.rendezvous
        if rv.get_log_requests():
            log.msg("ws client connecting: %s" % (request.peer,))
        self._reactor = self.factory.reactor

    def onOpen(self):
        rv = self.factory.rendezvous
        self.send("welcome", welcome=rv.get_welcome())

    def onMessage(self, payload, isBinary):
        server_rx = time.time()
        msg = json.loads(payload.decode("utf-8"))
        try:
            if "type" not in msg:
                raise Error("missing 'type'")
            if "id" in msg:
                # Only ack clients modern enough to include [id]. Older ones
                # won't recognize the message, then they'll abort.
                self.send("ack", id=msg["id"])

            mtype = msg["type"]
            if mtype == "ping":
                return self.handle_ping(msg)
            if mtype == "bind":
                return self.handle_bind(msg)

            if not self._app:
                raise Error("Must bind first")
            if mtype == "list":
                return self.handle_list()
            if mtype == "allocate":
                return self.handle_allocate()
            if mtype == "claim":
                return self.handle_claim(msg)
            if mtype == "watch":
                return self.handle_watch(msg)
            if mtype == "add":
                return self.handle_add(msg, server_rx)
            if mtype == "release":
                return self.handle_release(msg)

            raise Error("Unknown type")
        except Error as e:
            self.send("error", error=e._explain, orig=msg)

    def handle_ping(self, msg):
        if "ping" not in msg:
            raise Error("ping requires 'ping'")
        self.send("pong", pong=msg["ping"])

    def handle_bind(self, msg):
        if self._app or self._side:
            raise Error("already bound")
        if "appid" not in msg:
            raise Error("bind requires 'appid'")
        if "side" not in msg:
            raise Error("bind requires 'side'")
        self._app = self.factory.rendezvous.get_app(msg["appid"])
        self._side = msg["side"]

    def handle_list(self):
        channelids = sorted(self._app.get_claimed())
        self.send("channelids", channelids=channelids)

    def handle_allocate(self):
        if self._did_allocate:
            raise Error("You already allocated one channel, don't be greedy")
        channelid = self._app.find_available_channelid()
        assert isinstance(channelid, type(u""))
        self._did_allocate = True
        channel = self._app.claim_channel(channelid, self._side)
        self._channels[channelid] = channel
        self.send("allocated", channelid=channelid)

    def handle_claim(self, msg):
        if "channelid" not in msg:
            raise Error("claim requires 'channelid'")
        channelid = msg["channelid"]
        assert isinstance(channelid, type(u"")), type(channelid)
        if channelid not in self._channels:
            channel = self._app.claim_channel(channelid, self._side)
            self._channels[channelid] = channel

    def handle_watch(self, msg):
        channelid = msg["channelid"]
        if channelid not in self._channels:
            raise Error("must claim channel before watching")
        assert isinstance(channelid, type(u""))
        channel = self._channels[channelid]
        def _send(event):
            self.send("message", channelid=channelid, message=event)
        def _stop():
            self._reactor.callLater(0, self.transport.loseConnection)
        for old_message in channel.add_listener(self, _send, _stop):
            _send(old_message)

    def handle_add(self, msg, server_rx):
        channelid = msg["channelid"]
        if channelid not in self._channels:
            raise Error("must claim channel before adding")
        assert isinstance(channelid, type(u""))
        channel = self._channels[channelid]
        if "phase" not in msg:
            raise Error("missing 'phase'")
        if "body" not in msg:
            raise Error("missing 'body'")
        msgid = msg.get("id") # optional
        channel.add_message(self._side, msg["phase"], msg["body"],
                            server_rx, msgid)

    def handle_release(self, msg):
        channelid = msg["channelid"]
        if channelid not in self._channels:
            raise Error("must claim channel before releasing")
        assert isinstance(channelid, type(u""))
        channel = self._channels[channelid]
        deleted = channel.release(self._side, msg.get("mood"))
        del self._channels[channelid]
        self.send("released", status="deleted" if deleted else "waiting")

    def send(self, mtype, **kwargs):
        kwargs["type"] = mtype
        kwargs["server_tx"] = time.time()
        payload = json.dumps(kwargs).encode("utf-8")
        self.sendMessage(payload, False)

    def onClose(self, wasClean, code, reason):
        pass


class WebSocketRendezvousFactory(websocket.WebSocketServerFactory):
    protocol = WebSocketRendezvous
    def __init__(self, url, rendezvous):
        websocket.WebSocketServerFactory.__init__(self, url)
        self.rendezvous = rendezvous
        self.reactor = reactor # for tests to control
