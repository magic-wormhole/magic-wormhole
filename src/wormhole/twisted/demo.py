import sys, json
from twisted.internet import reactor
from .transcribe import Wormhole
from .. import public_relay

APPID = "lothar.com/wormhole/text-xfer"

w = Wormhole(APPID, public_relay.RENDEZVOUS_RELAY)

if sys.argv[1] == "send-text":
    message = sys.argv[2]
    data = json.dumps({"message": message}).encode("utf-8")
    d = w.get_code()
    def _got_code(code):
        print "code is:", code
        return w.get_data(data)
    d.addCallback(_got_code)
    def _got_data(them_bytes):
        them_d = json.loads(them_bytes.decode("utf-8"))
        if them_d["message"] == "ok":
            print "text sent"
        else:
            print "error sending text: %r" % (them_d,)
    d.addCallback(_got_data)
elif sys.argv[1] == "receive-text":
    code = sys.argv[2]
    w.set_code(code)
    data = json.dumps({"message": "ok"}).encode("utf-8")
    d = w.get_data(data)
    def _got_data(them_bytes):
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            print >>sys.stderr, "ERROR: " + them_d["error"]
            return 1
        print them_d["message"]
    d.addCallback(_got_data)
else:
    raise ValueError("bad command")
d.addCallback(lambda _: reactor.stop())
reactor.run()
