# To use the web() option, you should do:
# * cd misc
# * npm install d3 zepto

from __future__ import print_function
import os, sys, time, json, random


streams = sys.argv[1:]
if len(streams) != 2:
    print("run like: python dump-timing.py tx.json rx.json")
    sys.exit(1)
# for now, require sender as first file, receiver as second
# later, allow use of only one file.

data = {}
for i,fn in enumerate(streams):
    name = ["send", "receive"][i]
    with open(fn, "rb") as f:
        events = json.load(f)
    data[name] = {"fn": os.path.basename(fn), "events": events}

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

