import json, time
from twisted.internet import reactor
from twisted.python import log
from autobahn.twisted import websocket

# Each WebSocket connection is bound to one "appid", one "side", and one
# "channelid". The connection's appid and side are set by the "bind" message
# (which must be the first message on the connection). The channelid is set
# by either a "allocate" message (where the server picks the channelid), or
# by a "claim" message (where the client picks it). All three values must be
# set before any other message (watch, add, deallocate) can be sent.

# All websocket messages are JSON-encoded. The client can send us "inbound"
# messages (marked as "->" below), which may (or may not) provoke immediate
# (or delayed) "outbound" messages (marked as "<-"). There is no guaranteed
# correlation between requests and responses. In this list, "A -> B" means
# that some time after A is received, at least one message of type B will be
# sent out.

# All outbound messages include a "sent" key, which is a float (seconds since
# epoch) with the server clock just before the outbound message was written
# to the socket.

# connection -> welcome
#  <- {type: "welcome", welcome: {}} # .welcome keys are all optional:
#        current_version: out-of-date clients display a warning
#        motd: all clients display message, then continue normally
#        error: all clients display mesage, then terminate with error
# -> {type: "bind", appid:, side:}
# -> {type: "list"} -> channelids
#  <- {type: "channelids", channelids: [int..]}
# -> {type: "allocate"} -> allocated
#  <- {type: "allocated", channelid: int}
# -> {type: "claim", channelid: int}
# -> {type: "watch"} -> message # sends old messages and more in future
#  <- {type: "message", message: {phase:, body:}} # body is hex
# -> {type: "add", phase: str, body: hex} # may send echo
# -> {type: "deallocate", mood: str} -> deallocated
#  <- {type: "deallocated", status: waiting|deleted}
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
        self._channel = None
        self._watching = False

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

            if not self._channel:
                raise Error("Must set channel first")
            if mtype == "watch":
                return self.handle_watch(self._channel, msg)
            if mtype == "add":
                return self.handle_add(self._channel, msg, server_rx)
            if mtype == "deallocate":
                return self.handle_deallocate(self._channel, msg)

            raise Error("Unknown type")
        except Error as e:
            self.send("error", error=e._explain, orig=msg)

    def send_rendezvous_event(self, event):
        self.send("message", message=event)

    def stop_rendezvous_watcher(self):
        self._reactor.callLater(0, self.transport.loseConnection)

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
        channelids = sorted(self._app.get_allocated())
        self.send("channelids", channelids=channelids)

    def handle_allocate(self):
        if self._channel:
            raise Error("Already bound to a channelid")
        channelid = self._app.find_available_channelid()
        self._channel = self._app.allocate_channel(channelid, self._side)
        self.send("allocated", channelid=channelid)

    def handle_claim(self, msg):
        if "channelid" not in msg:
            raise Error("claim requires 'channelid'")
        # we allow allocate+claim as long as they match
        if self._channel is not None:
            old_cid = self._channel.get_channelid()
            if msg["channelid"] != old_cid:
                raise Error("Already bound to channelid %d" % old_cid)
        self._channel = self._app.allocate_channel(msg["channelid"], self._side)

    def handle_watch(self, channel, msg):
        if self._watching:
            raise Error("already watching")
        self._watching = True
        for old_message in channel.add_listener(self):
            self.send_rendezvous_event(old_message)

    def handle_add(self, channel, msg, server_rx):
        if "phase" not in msg:
            raise Error("missing 'phase'")
        if "body" not in msg:
            raise Error("missing 'body'")
        msgid = msg.get("id") # optional
        channel.add_message(self._side, msg["phase"], msg["body"],
                            server_rx, msgid)

    def handle_deallocate(self, channel, msg):
        deleted = channel.deallocate(self._side, msg.get("mood"))
        self.send("deallocated", status="deleted" if deleted else "waiting")

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
