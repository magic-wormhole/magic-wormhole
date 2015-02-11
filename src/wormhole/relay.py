import re, json
from collections import defaultdict
from twisted.python import log
from twisted.application import strports, service
from twisted.web import server, static, resource, http

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

class Root(resource.Resource):
    # child_FOO is a nevow thing, not a twisted.web.resource thing
    def __init__(self):
        resource.Resource.__init__(self)
        self.putChild("", static.Data("Wormhole Relay\n", "text/plain"))

class RelayServer(service.MultiService):
    def __init__(self, listenport):
        service.MultiService.__init__(self)
        self.root = Root()
        site = server.Site(self.root)
        self.port_service = strports.service(listenport, site)
        self.port_service.setServiceParent(self)
        self.relay = Relay() # for tests
        self.root.putChild("relay", self.relay)

    def get_root(self):
        return self.root

application = service.Application("foo")
RelayServer("tcp:8009").setServiceParent(application)
