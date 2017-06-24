from __future__ import unicode_literals
import time
from twisted.internet import reactor
from twisted.python import log
from autobahn.twisted import websocket
from .rendezvous import CrowdedError, ReclaimedError, SidedMessage
from ..util import dict_to_bytes, bytes_to_dict

# The WebSocket allows the client to send "commands" to the server, and the
# server to send "responses" to the client. Note that commands and responses
# are not necessarily one-to-one. All commands provoke an "ack" response
# (with a copy of the original message) for timing, testing, and
# synchronization purposes. All commands and responses are JSON-encoded.

# Each WebSocket connection is bound to one "appid" and one "side", which are
# set by the "bind" command (which must be the first command on the
# connection), and must be set before any other command will be accepted.

# Each connection can be bound to a single "mailbox" (a two-sided
# store-and-forward queue, identified by the "mailbox id": a long, randomly
# unique string identifier) by using the "open" command. This protects the
# mailbox from idle closure, enables the "add" command (to put new messages
# in the queue), and triggers delivery of past and future messages via the
# "message" response. The "close" command removes the binding (but note that
# it does not enable the subsequent binding of a second mailbox). When the
# last side closes a mailbox, its contents are deleted.

# Additionally, the connection can be bound a single "nameplate", which is
# short identifier that makes up the first component of a wormhole code. Each
# nameplate points to a single long-id "mailbox". The "allocate" message
# determines the shortest available numeric nameplate, reserves it, and
# returns the nameplate id. "list" returns a list of all numeric nameplates
# which currently have only one side active (i.e. they are waiting for a
# partner). The "claim" message reserves an arbitrary nameplate id (perhaps
# the receiver of a wormhole connection typed in a code they got from the
# sender, or perhaps the two sides agreed upon a code offline and are both
# typing it in), and the "release" message releases it. When every side that
# has claimed the nameplate has also released it, the nameplate is
# deallocated (but they will probably keep the underlying mailbox open).

# "claim" and "release" may only be called once per connection, however calls
# across connections (assuming a consistent "side") are idempotent. [connect,
# claim, disconnect, connect, claim] is legal, but not useful, as is a
# "release" for a nameplate that nobody is currently claiming.

# "open" and "close" may only be called once per connection. They are
# basically idempotent, however "open" doubles as a subscribe action. So
# [connect, open, disconnect, connect, open] is legal *and* useful (without
# the second "open", the second connection would not be subscribed to hear
# about new messages).

# Inbound (client to server) commands are marked as "->" below. Unrecognized
# inbound keys will be ignored. Outbound (server to client) responses use
# "<-". There is no guaranteed correlation between requests and responses. In
# this list, "A -> B" means that some time after A is received, at least one
# message of type B will be sent out (probably).

# All responses include a "server_tx" key, which is a float (seconds since
# epoch) with the server clock just before the outbound response was written
# to the socket.

# connection -> welcome
#  <- {type: "welcome", welcome: {}} # .welcome keys are all optional:
#        current_cli_version: out-of-date clients display a warning
#        motd: all clients display message, then continue normally
#        error: all clients display mesage, then terminate with error
# -> {type: "bind", appid:, side:}
#
# -> {type: "list"} -> nameplates
#  <- {type: "nameplates", nameplates: [{id: str,..},..]}
# -> {type: "allocate"} -> nameplate, mailbox
#  <- {type: "allocated", nameplate: str}
# -> {type: "claim", nameplate: str} -> mailbox
#  <- {type: "claimed", mailbox: str}
# -> {type: "release"}
#     .nameplate is optional, but must match previous claim()
#  <- {type: "released"}
#
# -> {type: "open", mailbox: str} -> message
#     sends old messages now, and subscribes to deliver future messages
#  <- {type: "message", side:, phase:, body:, msg_id:}} # body is hex
# -> {type: "add", phase: str, body: hex} # will send echo in a "message"
#
# -> {type: "close", mood: str} -> closed
#     .mailbox is optional, but must match previous open()
#  <- {type: "closed"}
#
#  <- {type: "error", error: str, orig: {}} # in response to malformed msgs

# for tests that need to know when a message has been processed:
# -> {type: "ping", ping: int} -> pong (does not require bind/claim)
#  <- {type: "pong", pong: int}

# XXX for now/debugging etc
do_mitigation = True


class Error(Exception):
    def __init__(self, explain):
        self._explain = explain

class WebSocketRendezvous(websocket.WebSocketServerProtocol):
    def __init__(self):
        websocket.WebSocketServerProtocol.__init__(self)
        self._app = None
        self._side = None
        self._did_allocate = False # only one allocate() per websocket
        self._listening = False
        self._did_claim = False
        self._nameplate_id = None
        self._did_release = False
        self._did_open = False
        self._mailbox = None
        self._mailbox_id = None
        self._did_close = False
        self._abilities = None
        self._is_old_client = None

    def onConnect(self, request):
        rv = self.factory.rendezvous
        if rv.get_log_requests():
            log.msg("ws client connecting: %s" % (request.peer,))
        self._reactor = self.factory.reactor

    def onMessage(self, payload, isBinary):
        server_rx = time.time()
        msg = bytes_to_dict(payload)
        try:
            if "type" not in msg:
                raise Error("missing 'type'")
            self.send("ack", id=msg.get("id"))

            # XXX we are relying on BIND looking for mitigation-tokens
            # and the fact that it must be first...
            mtype = msg["type"]
            if mtype == "ping":
                return self.handle_ping(msg)
            if mtype == "bind":
                return self.handle_bind(msg)
            if mtype == "abilities":
                return self.handle_abilities(msg)
            if mtype == "submit-permission":
                return self.handle_permission(msg)

            if not self._app:
                raise Error("must bind first")
            if mtype == "list":
                return self.handle_list()
            if mtype == "allocate":
                return self.handle_allocate(server_rx)
            if mtype == "claim":
                return self.handle_claim(msg, server_rx)
            if mtype == "release":
                return self.handle_release(msg, server_rx)

            if mtype == "open":
                return self.handle_open(msg, server_rx)
            if mtype == "add":
                return self.handle_add(msg, server_rx)
            if mtype == "close":
                return self.handle_close(msg, server_rx)

            raise Error("unknown type")
        except Error as e:
            self.send("error", error=e._explain, orig=msg)

    def handle_ping(self, msg):
        if "ping" not in msg:
            raise Error("ping requires 'ping'")
        self.send("pong", pong=msg["ping"])

    def handle_abilities(self, msg):
        self._abilities = msg
        self._is_old_client = False
        self.send("welcome", welcome=self.factory.rendezvous.get_welcome())

    def handle_permission(self, msg):
        token = msg.get("token", None)
        if not do_mitigation:
            pass  # XXX error, because we didn't ask for tokens?
        if not self.factory.rendezvous.is_valid_token(token):
            raise Error("Invalid token")
        else:
            self.send("granted", granted={})

    def handle_bind(self, msg):
        # XXX shouldn't this go in "app" or something? maybe not
        # .. feels odd to be in "_websocket.py" though
        if self._abilities is None:
            self._is_old_client = True
            self._abilities = dict()
            # we delay sending "welcome" until we get an ABILITIES
            # from the client. In this case, we've detected an old
            # client -- so if we're demanding mitigation-tokens right
            # now, it's an error.
            if do_mitigation:
                welcome_msg = {
                    u"error": u"We are under denial of service and need a token but your client doesn't support sending such tokens."
                }
            else:
                welcome_msg = self.factory.rendezvous.get_welcome()
            self.send("welcome", welcome=welcome_msg)

        if self._app or self._side:
            raise Error("already bound")
        if "appid" not in msg:
            raise Error("bind requires 'appid'")
        if "side" not in msg:
            raise Error("bind requires 'side'")
        self._app = self.factory.rendezvous.get_app(msg["appid"])
        self._side = msg["side"]


    def handle_list(self):
        nameplate_ids = sorted(self._app.get_nameplate_ids())
        # provide room to add nameplate attributes later (like which wordlist
        # is used for each, maybe how many words)
        nameplates = [{"id": nid} for nid in nameplate_ids]
        self.send("nameplates", nameplates=nameplates)

    def handle_allocate(self, server_rx):
        if self._did_allocate:
            raise Error("you already allocated one, don't be greedy")
        nameplate_id = self._app.allocate_nameplate(self._side, server_rx)
        assert isinstance(nameplate_id, type(""))
        self._did_allocate = True
        self.send("allocated", nameplate=nameplate_id)

    def handle_claim(self, msg, server_rx):
        if "nameplate" not in msg:
            raise Error("claim requires 'nameplate'")
        if self._did_claim:
            raise Error("only one claim per connection")
        self._did_claim = True
        nameplate_id = msg["nameplate"]
        assert isinstance(nameplate_id, type("")), type(nameplate_id)
        self._nameplate_id = nameplate_id
        try:
            mailbox_id = self._app.claim_nameplate(nameplate_id, self._side,
                                                   server_rx)
        except CrowdedError:
            raise Error("crowded")
        except ReclaimedError:
            raise Error("reclaimed")
        self.send("claimed", mailbox=mailbox_id)

    def handle_release(self, msg, server_rx):
        if self._did_release:
            raise Error("only one release per connection")
        if "nameplate" in msg:
            if self._nameplate_id is not None:
                if msg["nameplate"] != self._nameplate_id:
                    raise Error("release and claim must use same nameplate")
            nameplate_id = msg["nameplate"]
        else:
            if self._nameplate_id is None:
                raise Error("release without nameplate must follow claim")
            nameplate_id = self._nameplate_id
        assert nameplate_id is not None
        self._did_release = True
        self._app.release_nameplate(nameplate_id, self._side, server_rx)
        self.send("released")


    def handle_open(self, msg, server_rx):
        if self._mailbox:
            raise Error("only one open per connection")
        if "mailbox" not in msg:
            raise Error("open requires 'mailbox'")
        mailbox_id = msg["mailbox"]
        assert isinstance(mailbox_id, type(""))
        self._mailbox_id = mailbox_id
        self._mailbox = self._app.open_mailbox(mailbox_id, self._side,
                                               server_rx)
        def _send(sm):
            self.send("message", side=sm.side, phase=sm.phase,
                      body=sm.body, server_rx=sm.server_rx, id=sm.msg_id)
        def _stop():
            pass
        self._listening = True
        for old_sm in self._mailbox.add_listener(self, _send, _stop):
            _send(old_sm)

    def handle_add(self, msg, server_rx):
        if not self._mailbox:
            raise Error("must open mailbox before adding")
        if "phase" not in msg:
            raise Error("missing 'phase'")
        if "body" not in msg:
            raise Error("missing 'body'")
        msg_id = msg.get("id") # optional
        sm = SidedMessage(side=self._side, phase=msg["phase"],
                          body=msg["body"], server_rx=server_rx,
                          msg_id=msg_id)
        self._mailbox.add_message(sm)

    def handle_close(self, msg, server_rx):
        if self._did_close:
            raise Error("only one close per connection")
        if "mailbox" in msg:
            if self._mailbox_id is not None:
                if msg["mailbox"] != self._mailbox_id:
                    raise Error("open and close must use same mailbox")
            mailbox_id = msg["mailbox"]
        else:
            if self._mailbox_id is None:
                raise Error("close without mailbox must follow open")
            mailbox_id = self._mailbox_id
        if not self._mailbox:
            self._mailbox = self._app.open_mailbox(mailbox_id, self._side,
                                                   server_rx)
        if self._listening:
            self._mailbox.remove_listener(self)
            self._listening = False
        self._did_close = True
        self._mailbox.close(self._side, msg.get("mood"), server_rx)
        self._mailbox = None
        self.send("closed")

    def send(self, mtype, **kwargs):
        kwargs["type"] = mtype
        kwargs["server_tx"] = time.time()
        payload = dict_to_bytes(kwargs)
        self.sendMessage(payload, False)

    def onClose(self, wasClean, code, reason):
        #log.msg("onClose", self, self._mailbox, self._listening)
        if self._mailbox and self._listening:
            self._mailbox.remove_listener(self)


class WebSocketRendezvousFactory(websocket.WebSocketServerFactory):
    protocol = WebSocketRendezvous

    def __init__(self, url, rendezvous):
        websocket.WebSocketServerFactory.__init__(self, url)
        self.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        self.rendezvous = rendezvous
        self.reactor = reactor # for tests to control
