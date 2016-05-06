import json, time
from twisted.web import server, resource
from twisted.python import log

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

    def send_rendezvous_event(self, data):
        data = data.copy()
        data["sent"] = time.time()
        self.sendEvent(json.dumps(data))
    def stop_rendezvous_watcher(self):
        self.stop()

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
    def __init__(self, rendezvous):
        resource.Resource.__init__(self)
        self._rendezvous = rendezvous
        self._welcome = rendezvous.get_welcome()

class ChannelLister(RelayResource):
    def render_GET(self, request):
        if b"appid" not in request.args:
            e = NeedToUpgradeErrorResource(self._welcome)
            return e.get_message()
        appid = request.args[b"appid"][0].decode("utf-8")
        #print("LIST", appid)
        app = self._rendezvous.get_app(appid)
        allocated = app.get_allocated()
        data = {"welcome": self._welcome, "channelids": sorted(allocated),
                "sent": time.time()}
        return json_response(request, data)

class Allocator(RelayResource):
    def render_POST(self, request):
        content = request.content.read()
        data = json.loads(content.decode("utf-8"))
        appid = data["appid"]
        side = data["side"]
        if not isinstance(side, type(u"")):
            raise TypeError("side must be string, not '%s'" % type(side))
        #print("ALLOCATE", appid, side)
        app = self._rendezvous.get_app(appid)
        channelid = app.find_available_channelid()
        app.allocate_channel(channelid, side)
        if self._rendezvous.get_log_requests():
            log.msg("allocated #%d, now have %d DB channels" %
                    (channelid, len(app.get_allocated())))
        response = {"welcome": self._welcome, "channelid": channelid,
                    "sent": time.time()}
        return json_response(request, response)

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

        app = self._rendezvous.get_app(appid)
        channel = app.get_channel(channelid)
        messages = channel.add_message(side, phase, body, time.time(), None)
        response = {"welcome": self._welcome, "messages": messages,
                    "sent": time.time()}
        return json_response(request, response)

class GetterOrWatcher(RelayResource):
    def render_GET(self, request):
        appid = request.args[b"appid"][0].decode("utf-8")
        channelid = int(request.args[b"channelid"][0])
        #print("GET", appid, channelid)
        app = self._rendezvous.get_app(appid)
        channel = app.get_channel(channelid)

        if b"text/event-stream" not in (request.getHeader(b"accept") or b""):
            messages = channel.get_messages()
            response = {"welcome": self._welcome, "messages": messages,
                        "sent": time.time()}
            return json_response(request, response)

        request.setHeader(b"content-type", b"text/event-stream; charset=utf-8")
        ep = EventsProtocol(request)
        ep.sendEvent(json.dumps(self._welcome), name="welcome")
        old_events = channel.add_listener(ep)
        request.notifyFinish().addErrback(lambda f:
                                          channel.remove_listener(ep))
        for old_event in old_events:
            ep.send_rendezvous_event(old_event)
        return server.NOT_DONE_YET

class Watcher(RelayResource):
    def render_GET(self, request):
        appid = request.args[b"appid"][0].decode("utf-8")
        channelid = int(request.args[b"channelid"][0])
        app = self._rendezvous.get_app(appid)
        channel = app.get_channel(channelid)
        if b"text/event-stream" not in (request.getHeader(b"accept") or b""):
            raise TypeError("/watch is for EventSource only")

        request.setHeader(b"content-type", b"text/event-stream; charset=utf-8")
        ep = EventsProtocol(request)
        ep.sendEvent(json.dumps(self._welcome), name="welcome")
        old_events = channel.add_listener(ep)
        request.notifyFinish().addErrback(lambda f:
                                          channel.remove_listener(ep))
        for old_event in old_events:
            ep.send_rendezvous_event(old_event)
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

        app = self._rendezvous.get_app(appid)
        channel = app.get_channel(channelid)
        deleted = channel.deallocate(side, mood)
        response = {"status": "waiting", "sent": time.time()}
        if deleted:
            response = {"status": "deleted", "sent": time.time()}
        return json_response(request, response)


class WebRendezvous(resource.Resource):
    def __init__(self, rendezvous):
        resource.Resource.__init__(self)
        self._rendezvous = rendezvous
        self.putChild(b"list", ChannelLister(rendezvous))
        self.putChild(b"allocate", Allocator(rendezvous))
        self.putChild(b"add", Adder(rendezvous))
        self.putChild(b"get", GetterOrWatcher(rendezvous))
        self.putChild(b"watch", Watcher(rendezvous))
        self.putChild(b"deallocate", Deallocator(rendezvous))

    def getChild(self, path, req):
        # 0.4.0 used "POST /CID/SIDE/post/MSGNUM"
        # 0.5.0 replaced it with "POST /add (json body)"
        # give a nicer error message to old clients
        if (len(req.postpath) >= 2
            and req.postpath[1] in (b"post", b"poll", b"deallocate")):
            welcome = self._rendezvous.get_welcome()
            return NeedToUpgradeErrorResource(welcome)
        return resource.NoResource("No such child resource.")
