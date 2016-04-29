# To use the web() option, you should do:
# * cd misc
# * npm install d3 zepto

from __future__ import print_function
import os, sys, time, json, random


streams = sys.argv[1:]
if not streams:
    print("run like: python dump-timing.py tx.json rx.json")
    sys.exit(1)
num_streams = len(streams)
labels = dict([(num, " "*num + "[%d]" % (num+1) + " "*(num_streams-1-num))
               for num in range(num_streams)])
abs_timeline = []
sides = []

for side,fn in enumerate(streams):
    with open(fn, "rb") as f:
        for (start, sent, finish, what, details) in json.load(f):
            abs_timeline.append( (start, sent, finish, side, what, details) )
    print("%s is %s" % (labels[side], fn))
    sides.append(os.path.basename(fn))

# relativize all timestamps
all_times = [e[0] for e in abs_timeline] + [e[2] for e in abs_timeline if e[2]]
all_times.sort()
earliest = all_times[0]
def rel(t):
    if t is None: return None
    return t - earliest
timeline = [ (rel(start), rel(sent), rel(finish), side, what, details)
             for (start, sent, finish, side, what, details)
             in abs_timeline ]
data = {}

# we pre-calculate the "lane" that each item uses, here in python, rather
# than leaving that up to the javascript.
data["lanes"] = ["proc", # 0 gets high-level events and spans: process start,
                         # imports, command dispatch, code established, key
                         # established, transit connected, process exit
                 "API", # 1 gets API call spans (apps usually only wait for
                        # one at a time, so they won't overlap): get_code,
                        # input_code, get_verifier, get_data, send_data,
                        # close
                 "wait", # 2 shows waiting-for-human: input code, get
                         # permission
                 "app", # 3: file-xfer events
                 "skt", # 4: websocket message send/receives
                 "misc", # 5: anything else
                 ]
data["bounds"] = {"min": rel(all_times[0]), "max": rel(all_times[-1]),
                  }
data["sides"] = sides
print("started at %s" % time.ctime(earliest))
print("duration %s seconds" % data["bounds"]["max"])
items = data["items"] = []

for num, (start, sent, finish, side, what, details) in enumerate(timeline):
    background = False
    if what in ["wormhole",]:
        # background region for wormhole lifetime
        lane = 0
        background = True
    elif what in ["process start", "import", "command dispatch",
                  "code established", "key established", "transit connected",
                  "exit"]:
        lane = 0
    elif what in ["API get_code", "API input_code", "API set_code",
                  "API get_verifier", "API get_data", "API send_data",
                  #"API get data", "API send data",
                  "API close"]:
        lane = 1
    elif details.get("waiting") in ["user", "crypto"]: # permission or math
        lane = 2
    elif what in ["tx file", "get ack", "rx file", "unpack zip", "send ack"]:
        lane = 3
    elif what in ["websocket"]:
        # connection establishment
        lane = 4
        background = True
    elif (what in ["welcome", "error"] # rendezvous message receives
          or what in ["allocate", "list", "get", "add", "deallocate"]
          # rendezvous message sends
          ):
        lane = 4
    else:
        lane = 5 # unknown

    if background:
        continue # disable until I figure out how to draw these better

    details_str = ", ".join(["%s=%s" % (name, details[name])
                             for name in sorted(details)])

    items.append({"side": side,
                  "lane": lane,
                  "start_time": start,
                  "server_sent": sent,
                  "finish_time": finish, # maybe None
                  "what": what,
                  "details": details,
                  "details_str": details_str,
                  "wiggle": random.randint(0,4),
                  })

    #if "waiting" in details:
    #    viz_className += " wait-%s" % details["waiting"]

from pprint import pprint
pprint(data)

here = os.path.dirname(__file__)
web_root = os.path.join(here, "web")
lib_root = os.path.join(here, "node_modules")
if not os.path.isdir(lib_root):
    print("Cannot find 'd3' and 'd3-tip' in misc/node_modules/")
    print("Please run 'npm install d3 d3-tip zepto' from the misc/ directory.")
    sys.exit(1)

def web():
    # set up a server that serves web/ at the root, plus a /data.json built
    # from {timeline}. Quit when it fetches /done .
    from twisted.web import resource, static, server
    from twisted.internet import reactor, endpoints
    ep = endpoints.serverFromString(reactor, "tcp:8066:interface=127.0.0.1")
    root = static.File(web_root)
    root.putChild("data.json", static.Data(json.dumps(data).encode("utf-8"),
                                           "application/json"))
    root.putChild("lib", static.File(lib_root))
    class Shutdown(resource.Resource):
        def render_GET(self, request):
            if 0:
                print("timeline ready, server shutting down")
                reactor.stop()
            return "shutting down"
    root.putChild("done", Shutdown())
    site = server.Site(root)
    ep.listen(site)
    import webbrowser
    def launch_browser():
        webbrowser.open("http://localhost:%d/timeline.html" % 8066)
        print("browser opened, waiting for shutdown")
    reactor.callLater(0, launch_browser)
    reactor.run()
web()

