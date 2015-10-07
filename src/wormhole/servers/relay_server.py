from __future__ import print_function
import json, time, random
from twisted.python import log
from twisted.application import service, internet
from twisted.web import server, resource

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
DAY = 24*HOUR
MB = 1000*1000

CHANNEL_EXPIRATION_TIME = 3*DAY
EXPIRATION_CHECK_PERIOD = 2*HOUR

def json_response(request, data):
    request.setHeader(b"content-type", b"application/json; charset=utf-8")
    return (json.dumps(data)+"\n").encode("utf-8")

class EventsProtocol:
    def __init__(self, request):
        self.request = request

    def sendComment(self, comment):
        # this is ignored by clients, but can keep the connection open in the
        # face of firewall/NAT timeouts. It also helps unit tests, since
        # apparently twisted.web.client.Agent doesn't consider the connection
        # to be established until it sees the first byte of the reponse body.
        self.request.write(b": " + comment + b"\n\n")

    def sendEvent(self, data, name=None, id=None, retry=None):
        if name:
            self.request.write(b"event: " + name.encode("utf-8") + b"\n")
            # e.g. if name=foo, then the client web page should do:
            # (new EventSource(url)).addEventListener("foo", handlerfunc)
            # Note that this basically defaults to "message".
        if id:
            self.request.write(b"id: " + id.encode("utf-8") + b"\n")
        if retry:
            self.request.write(b"retry: " + retry + b"\n") # milliseconds
        for line in data.splitlines():
            self.request.write(b"data: " + line.encode("utf-8") + b"\n")
        self.request.write(b"\n")

    def stop(self):
        self.request.finish()

# note: no versions of IE (including the current IE11) support EventSource

# relay URLs are as follows:   (MESSAGES=[{phase:,body:}..])
#  GET /list?appid=                                 -> {channelids: [INT..]}
#  POST /allocate {appid:,side:}                    -> {channelid: INT}
#   these return all messages (base64) for appid=/channelid= :
#  POST /add {appid:,channelid:,side:,phase:,body:} -> {messages: MESSAGES}
#  GET  /get?appid=&channelid= (no-eventsource)     -> {messages: MESSAGES}
#  GET  /get?appid=&channelid= (eventsource)        -> {phase:, body:}..
#  POST /deallocate {appid:,channelid:,side:} -> {status: waiting | deleted}
# all JSON responses include a "welcome:{..}" key

class ChannelLister(resource.Resource):
    def __init__(self, relay):
        resource.Resource.__init__(self)
        self._relay = relay

    def render_GET(self, request):
        appid = request.args[b"appid"][0].decode("utf-8")
        #print("LIST", appid)
        app = self._relay.get_app(appid)
        allocated = app.get_allocated()
        request.setHeader(b"content-type", b"application/json; charset=utf-8")
        data = {"welcome": self._relay.welcome,
                "channelids": sorted(allocated)}
        return (json.dumps(data)+"\n").encode("utf-8")

class Allocator(resource.Resource):
    def __init__(self, relay):
        resource.Resource.__init__(self)
        self._relay = relay

    def render_POST(self, request):
        content = request.content.read()
        data = json.loads(content.decode("utf-8"))
        appid = data["appid"]
        side = data["side"]
        if not isinstance(side, type(u"")):
            raise TypeError("side must be string, not '%s'" % type(side))
        #print("ALLOCATE", appid, side)
        app = self._relay.get_app(appid)
        channelid = app.find_available_channelid()
        app.allocate_channel(channelid, side)
        log.msg("allocated #%d, now have %d DB channels" %
                (channelid, len(app.get_allocated())))
        request.setHeader(b"content-type", b"application/json; charset=utf-8")
        data = {"welcome": self._relay.welcome,
                "channelid": channelid}
        return (json.dumps(data)+"\n").encode("utf-8")

class Adder(resource.Resource):
    def __init__(self, relay):
        resource.Resource.__init__(self)
        self._relay = relay

    def render_POST(self, request):
        #content = json.load(request.content, encoding="utf-8")
        content = request.content.read()
        data = json.loads(content.decode("utf-8"))
        appid = data["appid"]
        channelid = int(data["channelid"])
        side = data["side"]
        phase = data["phase"]
        if not isinstance(phase, type(u"")):
            raise TypeError("phase must be string, not %s" % type(phase))
        body = data["body"]
        #print("ADD", appid, channelid, side, phase, body)

        app = self._relay.get_app(appid)
        channel = app.get_channel(channelid)
        response = channel.add_message(side, phase, body)
        return json_response(request, response)

class Getter(resource.Resource):
    def __init__(self, relay):
        self._relay = relay

    def render_GET(self, request):
        appid = request.args[b"appid"][0].decode("utf-8")
        channelid = int(request.args[b"channelid"][0])
        #print("GET", appid, channelid)
        app = self._relay.get_app(appid)
        channel = app.get_channel(channelid)

        if b"text/event-stream" not in (request.getHeader(b"accept") or b""):
            response = channel.get_messages()
            return json_response(request, response)

        request.setHeader(b"content-type", b"text/event-stream; charset=utf-8")
        ep = EventsProtocol(request)
        ep.sendEvent(json.dumps(self._relay.welcome), name="welcome")
        old_events = channel.add_listener(ep.sendEvent)
        request.notifyFinish().addErrback(lambda f:
                                          channel.remove_listener(ep.sendEvent))
        for old_event in old_events:
            ep.sendEvent(old_event)
        return server.NOT_DONE_YET

class Deallocator(resource.Resource):
    def __init__(self, relay):
        self._relay = relay

    def render_POST(self, request):
        content = request.content.read()
        data = json.loads(content.decode("utf-8"))
        appid = data["appid"]
        channelid = int(data["channelid"])
        side = data["side"]
        #print("DEALLOCATE", appid, channelid, side)
        app = self._relay.get_app(appid)
        deleted = app.maybe_free_child(channelid, side)
        response = {"status": "waiting"}
        if deleted:
            response = {"status": "deleted"}
        return json_response(request, response)

class Channel(resource.Resource):
    def __init__(self, relay, appid, channelid):
        resource.Resource.__init__(self)
        self._relay = relay
        self._appid = appid
        self._channelid = channelid
        self._listeners = set() # callbacks that take JSONable object

    def get_messages(self):
        messages = []
        db = self._relay.db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `appid`=? AND `channelid`=?"
                              " ORDER BY `when` ASC",
                              (self._appid, self._channelid)).fetchall():
            messages.append({"phase": row["phase"], "body": row["body"]})
        data = {"welcome": self._relay.welcome, "messages": messages}
        return data

    def add_listener(self, listener):
        self._listeners.add(listener)
        db = self._relay.db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `appid`=? AND `channelid`=?"
                              " ORDER BY `when` ASC",
                              (self._appid, self._channelid)).fetchall():
            yield json.dumps({"phase": row["phase"], "body": row["body"]})
    def remove_listener(self, listener):
        self._listeners.discard(listener)

    def broadcast_message(self, phase, body):
        data = json.dumps({"phase": phase, "body": body})
        for listener in self._listeners:
            listener(data)

    def add_message(self, side, phase, body):
        db = self._relay.db
        db.execute("INSERT INTO `messages`"
                   " (`appid`, `channelid`, `side`, `phase`,  `body`, `when`)"
                   " VALUES (?,?,?,?, ?,?)",
                   (self._appid, self._channelid, side, phase,
                    body, time.time()))
        db.execute("INSERT INTO `allocations`"
                   " (`appid`, `channelid`, `side`)"
                   " VALUES (?,?,?)",
                   (self._appid, self._channelid, side))
        db.commit()
        self.broadcast_message(phase, body)
        return self.get_messages()

class AppNamespace(resource.Resource):
    def __init__(self, relay, appid):
        resource.Resource.__init__(self)
        self._relay = relay
        self._appid = appid
        self._channels = {}

    def get_allocated(self):
        db = self._relay.db
        c = db.execute("SELECT DISTINCT `channelid` FROM `allocations`"
                       " WHERE `appid`=?", (self._appid,))
        return set([row["channelid"] for row in c.fetchall()])

    def find_available_channelid(self):
        allocated = self.get_allocated()
        for size in range(1,4): # stick to 1-999 for now
            available = set()
            for cid in range(10**(size-1), 10**size):
                if cid not in allocated:
                    available.add(cid)
            if available:
                return random.choice(list(available))
        # ouch, 999 currently allocated. Try random ones for a while.
        for tries in range(1000):
            cid = random.randrange(1000, 1000*1000)
            if cid not in allocated:
                return cid
        raise ValueError("unable to find a free channel-id")

    def allocate_channel(self, channelid, side):
        db = self._relay.db
        db.execute("INSERT INTO `allocations` VALUES (?,?,?)",
                   (self._appid, channelid, side))
        db.commit()

    def get_channel(self, channelid):
        assert isinstance(channelid, int)
        if not channelid in self._channels:
            log.msg("spawning #%d for appid %s" % (channelid, self._appid))
            self._channels[channelid] = Channel(self._relay,
                                                self._appid, channelid)
        return self._channels[channelid]

    def maybe_free_child(self, channelid, side):
        db = self._relay.db
        db.execute("DELETE FROM `allocations`"
                   " WHERE `appid`=? AND `channelid`=? AND `side`=?",
                   (self._appid, channelid, side))
        db.commit()
        remaining = db.execute("SELECT COUNT(*) FROM `allocations`"
                               " WHERE `appid`=? AND `channelid`=?",
                               (self._appid, channelid)).fetchone()[0]
        if remaining:
            return False
        self._free_child(channelid)
        return True

    def _free_child(self, channelid):
        db = self._relay.db
        db.execute("DELETE FROM `allocations`"
                   " WHERE `appid`=? AND `channelid`=?",
                   (self._appid, channelid))
        db.execute("DELETE FROM `messages`"
                   " WHERE `appid`=? AND `channelid`=?",
                   (self._appid, channelid))
        db.commit()
        if channelid in self._channels:
            self._channels.pop(channelid)
        log.msg("freed+killed #%d, now have %d DB channels, %d live" %
                (channelid, len(self.get_allocated()), len(self._channels)))

    def prune_old_channels(self):
        db = self._relay.db
        old = time.time() - CHANNEL_EXPIRATION_TIME
        for channelid in self.get_allocated():
            c = db.execute("SELECT `when` FROM `messages`"
                           " WHERE `appid`=? AND `channelid`=?"
                           " ORDER BY `when` DESC LIMIT 1",
                           (self._appid, channelid))
            rows = c.fetchall()
            if not rows or (rows[0]["when"] < old):
                log.msg("expiring %d" % channelid)
                self._free_child(channelid)
        return bool(self._channels)

class Relay(resource.Resource, service.MultiService):
    def __init__(self, db, welcome):
        resource.Resource.__init__(self)
        service.MultiService.__init__(self)
        self.db = db
        self.welcome = welcome
        self._apps = {}
        t = internet.TimerService(EXPIRATION_CHECK_PERIOD, self.prune)
        t.setServiceParent(self)
        self.putChild(b"list", ChannelLister(self))
        self.putChild(b"allocate", Allocator(self))
        self.putChild(b"add", Adder(self))
        self.putChild(b"get", Getter(self))
        self.putChild(b"deallocate", Deallocator(self))

    def get_app(self, appid):
        assert isinstance(appid, type(u""))
        if not appid in self._apps:
            log.msg("spawning appid %s" % (appid,))
            self._apps[appid] = AppNamespace(self, appid)
        return self._apps[appid]

    def prune(self):
        for appid in list(self._apps):
            still_active = self._apps[appid].prune_old_channels()
            if not still_active:
                self._apps.pop(appid)
