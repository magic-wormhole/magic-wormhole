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

ALLOCATE = u"_allocate"
DEALLOCATE = u"_deallocate"

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
# ("-" indicates a deprecated URL)
#  GET /list?appid=                                 -> {channelids: [INT..]}
#  POST /allocate {appid:,side:}                    -> {channelid: INT}
#   these return all messages (base64) for appid=/channelid= :
#  POST /add {appid:,channelid:,side:,phase:,body:} -> {messages: MESSAGES}
#  GET  /get?appid=&channelid= (no-eventsource)     -> {messages: MESSAGES}
#- GET  /get?appid=&channelid= (eventsource)        -> {phase:, body:}..
#  GET  /watch?appid=&channelid= (eventsource)      -> {phase:, body:}..
#  POST /deallocate {appid:,channelid:,side:} -> {status: waiting | deleted}
# all JSON responses include a "welcome:{..}" key

class RelayResource(resource.Resource):
    def __init__(self, relay, welcome, log_requests):
        resource.Resource.__init__(self)
        self._relay = relay
        self._welcome = welcome
        self._log_requests = log_requests

class ChannelLister(RelayResource):
    def render_GET(self, request):
        if b"appid" not in request.args:
            e = NeedToUpgradeErrorResource(self._welcome)
            return e.get_message()
        appid = request.args[b"appid"][0].decode("utf-8")
        #print("LIST", appid)
        app = self._relay.get_app(appid)
        allocated = app.get_allocated()
        request.setHeader(b"content-type", b"application/json; charset=utf-8")
        data = {"welcome": self._welcome, "channelids": sorted(allocated)}
        return (json.dumps(data)+"\n").encode("utf-8")

class Allocator(RelayResource):
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
        if self._log_requests:
            log.msg("allocated #%d, now have %d DB channels" %
                    (channelid, len(app.get_allocated())))
        request.setHeader(b"content-type", b"application/json; charset=utf-8")
        data = {"welcome": self._welcome, "channelid": channelid}
        return (json.dumps(data)+"\n").encode("utf-8")

    def getChild(self, path, req):
        # wormhole-0.4.0 "send" started with "POST /allocate/SIDE".
        # wormhole-0.5.0 changed that to "POST /allocate". We catch the old
        # URL here to deliver a nicer error message (with upgrade
        # instructions) than an ugly 404.
        return NeedToUpgradeErrorResource(self._welcome)

class NeedToUpgradeErrorResource(resource.Resource):
    def __init__(self, welcome):
        resource.Resource.__init__(self)
        w = welcome.copy()
        w["error"] = "Sorry, you must upgrade your client to use this server."
        message = {"welcome": w}
        self._message = (json.dumps(message)+"\n").encode("utf-8")
    def get_message(self):
        return self._message
    def render_POST(self, request):
        return self._message
    def render_GET(self, request):
        return self._message
    def getChild(self, path, req):
        return self

class Adder(RelayResource):
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
        # response is generated with get_messages(), so it includes both
        # 'welcome' and 'messages'
        return json_response(request, response)

class GetterOrWatcher(RelayResource):
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
        ep.sendEvent(json.dumps(self._welcome), name="welcome")
        old_events = channel.add_listener(ep.sendEvent)
        request.notifyFinish().addErrback(lambda f:
                                          channel.remove_listener(ep.sendEvent))
        for old_event in old_events:
            ep.sendEvent(old_event)
        return server.NOT_DONE_YET

class Watcher(RelayResource):
    def render_GET(self, request):
        appid = request.args[b"appid"][0].decode("utf-8")
        channelid = int(request.args[b"channelid"][0])
        app = self._relay.get_app(appid)
        channel = app.get_channel(channelid)
        if b"text/event-stream" not in (request.getHeader(b"accept") or b""):
            raise TypeError("/watch is for EventSource only")

        request.setHeader(b"content-type", b"text/event-stream; charset=utf-8")
        ep = EventsProtocol(request)
        ep.sendEvent(json.dumps(self._welcome), name="welcome")
        old_events = channel.add_listener(ep.sendEvent)
        request.notifyFinish().addErrback(lambda f:
                                          channel.remove_listener(ep.sendEvent))
        for old_event in old_events:
            ep.sendEvent(old_event)
        return server.NOT_DONE_YET

class Deallocator(RelayResource):
    def render_POST(self, request):
        content = request.content.read()
        data = json.loads(content.decode("utf-8"))
        appid = data["appid"]
        channelid = int(data["channelid"])
        side = data["side"]
        if not isinstance(side, type(u"")):
            raise TypeError("side must be string, not '%s'" % type(side))
        mood = data.get("mood")
        #print("DEALLOCATE", appid, channelid, side)

        app = self._relay.get_app(appid)
        channel = app.get_channel(channelid)
        deleted = channel.deallocate(side, mood)
        response = {"status": "waiting"}
        if deleted:
            response = {"status": "deleted"}
        return json_response(request, response)



class Channel:
    def __init__(self, app, db, welcome, blur_usage, log_requests,
                 appid, channelid):
        self._app = app
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._appid = appid
        self._channelid = channelid
        self._listeners = set() # callbacks that take JSONable object

    def get_messages(self):
        messages = []
        db = self._db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `appid`=? AND `channelid`=?"
                              " ORDER BY `when` ASC",
                              (self._appid, self._channelid)).fetchall():
            if row["phase"] in (u"_allocate", u"_deallocate"):
                continue
            messages.append({"phase": row["phase"], "body": row["body"]})
        data = {"welcome": self._welcome, "messages": messages}
        return data

    def add_listener(self, listener):
        self._listeners.add(listener)
        db = self._db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `appid`=? AND `channelid`=?"
                              " ORDER BY `when` ASC",
                              (self._appid, self._channelid)).fetchall():
            if row["phase"] in (u"_allocate", u"_deallocate"):
                continue
            yield json.dumps({"phase": row["phase"], "body": row["body"]})
    def remove_listener(self, listener):
        self._listeners.discard(listener)

    def broadcast_message(self, phase, body):
        data = json.dumps({"phase": phase, "body": body})
        for listener in self._listeners:
            listener(data)

    def _add_message(self, side, phase, body):
        db = self._db
        db.execute("INSERT INTO `messages`"
                   " (`appid`, `channelid`, `side`, `phase`,  `body`, `when`)"
                   " VALUES (?,?,?,?, ?,?)",
                   (self._appid, self._channelid, side, phase,
                    body, time.time()))
        db.commit()

    def allocate(self, side):
        self._add_message(side, ALLOCATE, None)

    def add_message(self, side, phase, body):
        self._add_message(side, phase, body)
        self.broadcast_message(phase, body)
        return self.get_messages()

    def deallocate(self, side, mood):
        self._add_message(side, DEALLOCATE, mood)
        db = self._db
        seen = set([row["side"] for row in
                    db.execute("SELECT `side` FROM `messages`"
                               " WHERE `appid`=? AND `channelid`=?",
                               (self._appid, self._channelid))])
        freed = set([row["side"] for row in
                     db.execute("SELECT `side` FROM `messages`"
                                " WHERE `appid`=? AND `channelid`=?"
                                " AND `phase`=?",
                                (self._appid, self._channelid, DEALLOCATE))])
        if seen - freed:
            return False
        self.delete_and_summarize()
        return True

    def is_idle(self):
        if self._listeners:
            return False
        c = self._db.execute("SELECT `when` FROM `messages`"
                             " WHERE `appid`=? AND `channelid`=?"
                             " ORDER BY `when` DESC LIMIT 1",
                             (self._appid, self._channelid))
        rows = c.fetchall()
        if not rows:
            return True
        old = time.time() - CHANNEL_EXPIRATION_TIME
        if rows[0]["when"] < old:
            return True
        return False

    def _store_summary(self, summary):
        (started, result, total_time, waiting_time) = summary
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
        self._db.execute("INSERT INTO `usage`"
                         " (`type`, `started`, `result`,"
                         "  `total_time`, `waiting_time`)"
                         " VALUES (?,?,?, ?,?)",
                         (u"rendezvous", started, result,
                          total_time, waiting_time))
        self._db.commit()

    def _summarize(self, messages, delete_time):
        all_sides = set([m["side"] for m in messages])
        if len(all_sides) == 0:
            log.msg("_summarize was given zero messages") # shouldn't happen
            return

        started = min([m["when"] for m in messages])
        # 'total_time' is how long the channel was occupied. That ends now,
        # both for channels that got pruned for inactivity, and for channels
        # that got pruned because of two DEALLOCATE messages
        total_time = delete_time - started

        if len(all_sides) == 1:
            return (started, "lonely", total_time, None)
        if len(all_sides) > 2:
            # TODO: it'll be useful to have more detail here
            return (started, "crowded", total_time, None)

        # exactly two sides were involved
        A_side = sorted(messages, key=lambda m: m["when"])[0]["side"]
        B_side = list(all_sides - set([A_side]))[0]

        # How long did the first side wait until the second side showed up?
        first_A = min([m["when"] for m in messages if m["side"] == A_side])
        first_B = min([m["when"] for m in messages if m["side"] == B_side])
        waiting_time = first_B - first_A

        # now, were all sides closed? If not, this is "pruney"
        A_deallocs = [m for m in messages
                      if m["phase"] == DEALLOCATE and m["side"] == A_side]
        B_deallocs = [m for m in messages
                      if m["phase"] == DEALLOCATE and m["side"] == B_side]
        if not A_deallocs or not B_deallocs:
            return (started, "pruney", total_time, None)

        # ok, both sides closed. figure out the mood
        A_mood = A_deallocs[0]["body"] # maybe None
        B_mood = B_deallocs[0]["body"] # maybe None
        mood = "quiet"
        if A_mood == u"happy" and B_mood == u"happy":
            mood = "happy"
        if A_mood == u"lonely" or B_mood == u"lonely":
            mood = "lonely"
        if A_mood == u"errory" or B_mood == u"errory":
            mood = "errory"
        if A_mood == u"scary" or B_mood == u"scary":
            mood = "scary"
        return (started, mood, total_time, waiting_time)

    def delete_and_summarize(self):
        db = self._db
        c = self._db.execute("SELECT * FROM `messages`"
                             " WHERE `appid`=? AND `channelid`=?"
                             " ORDER BY `when`",
                             (self._appid, self._channelid))
        messages = c.fetchall()
        summary = self._summarize(messages, time.time())
        self._store_summary(summary)
        db.execute("DELETE FROM `messages`"
                   " WHERE `appid`=? AND `channelid`=?",
                   (self._appid, self._channelid))
        db.commit()

        # It'd be nice to shut down any EventSource listeners here. But we
        # don't hang on to the EventsProtocol, so we can't really shut it
        # down here: any listeners will stick around until they shut down
        # from the client side. That will keep the Channel object in memory,
        # but it won't be reachable from the AppNamespace, so no further
        # messages will be sent to it. Eventually, when they close the TCP
        # connection, self.remove_listener() will be called, ep.sendEvent
        # will be removed from self._listeners, breaking the circular
        # reference, and everything will get freed.

        self._app.free_channel(self._channelid)


class AppNamespace:
    def __init__(self, db, welcome, blur_usage, log_requests, appid):
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._appid = appid
        self._channels = {}

    def get_allocated(self):
        db = self._db
        c = db.execute("SELECT DISTINCT `channelid` FROM `messages`"
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
        channel = self.get_channel(channelid)
        channel.allocate(side)
        return channel

    def get_channel(self, channelid):
        assert isinstance(channelid, int)
        if not channelid in self._channels:
            if self._log_requests:
                log.msg("spawning #%d for appid %s" % (channelid, self._appid))
            self._channels[channelid] = Channel(self, self._db, self._welcome,
                                                self._blur_usage,
                                                self._log_requests,
                                                self._appid, channelid)
        return self._channels[channelid]

    def free_channel(self, channelid):
        # called from Channel.delete_and_summarize(), which deletes any
        # messages

        if channelid in self._channels:
            self._channels.pop(channelid)
        if self._log_requests:
            log.msg("freed+killed #%d, now have %d DB channels, %d live" %
                    (channelid, len(self.get_allocated()), len(self._channels)))

    def prune_old_channels(self):
        # For now, pruning is logged even if log_requests is False, to debug
        # the pruning process, and since pruning is triggered by a timer
        # instead of by user action. It does reveal which channels were
        # present when the pruning process began, though, so in the log run
        # it should do less logging.
        log.msg("  channel prune begins")
        # a channel is deleted when there are no listeners and there have
        # been no messages added in CHANNEL_EXPIRATION_TIME seconds
        channels = set(self.get_allocated()) # these have messages
        channels.update(self._channels) # these might have listeners
        for channelid in channels:
            log.msg("   channel prune checking %d" % channelid)
            channel = self.get_channel(channelid)
            if channel.is_idle():
                log.msg("   channel prune expiring %d" % channelid)
                channel.delete_and_summarize() # calls self.free_channel
        log.msg("  channel prune done, %r left" % (self._channels.keys(),))
        return bool(self._channels)

class Relay(resource.Resource, service.MultiService):
    def __init__(self, db, welcome, blur_usage):
        resource.Resource.__init__(self)
        service.MultiService.__init__(self)
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        log_requests = blur_usage is None
        self._log_requests = log_requests
        self._apps = {}
        t = internet.TimerService(EXPIRATION_CHECK_PERIOD, self.prune)
        t.setServiceParent(self)
        self.putChild(b"list", ChannelLister(self, welcome, log_requests))
        self.putChild(b"allocate", Allocator(self, welcome, log_requests))
        self.putChild(b"add", Adder(self, welcome, log_requests))
        self.putChild(b"get", GetterOrWatcher(self, welcome, log_requests))
        self.putChild(b"watch", Watcher(self, welcome, log_requests))
        self.putChild(b"deallocate", Deallocator(self, welcome, log_requests))

    def getChild(self, path, req):
        # 0.4.0 used "POST /CID/SIDE/post/MSGNUM"
        # 0.5.0 replaced it with "POST /add (json body)"
        # give a nicer error message to old clients
        if (len(req.postpath) >= 2
            and req.postpath[1] in (b"post", b"poll", b"deallocate")):
            return NeedToUpgradeErrorResource(self._welcome)
        return resource.NoResource("No such child resource.")

    def get_app(self, appid):
        assert isinstance(appid, type(u""))
        if not appid in self._apps:
            if self._log_requests:
                log.msg("spawning appid %s" % (appid,))
            self._apps[appid] = AppNamespace(self._db, self._welcome,
                                             self._blur_usage,
                                             self._log_requests, appid)
        return self._apps[appid]

    def prune(self):
        # As with AppNamespace.prune_old_channels, we log for now.
        log.msg("beginning app prune")
        c = self._db.execute("SELECT DISTINCT `appid` FROM `messages`")
        apps = set([row["appid"] for row in c.fetchall()]) # these have messages
        apps.update(self._apps) # these might have listeners
        for appid in apps:
            log.msg(" app prune checking %r" % (appid,))
            still_active = self.get_app(appid).prune_old_channels()
            if not still_active:
                log.msg("prune pops app %r" % (appid,))
                self._apps.pop(appid)
        log.msg("app prune ends, %d remaining apps" % len(self._apps))
