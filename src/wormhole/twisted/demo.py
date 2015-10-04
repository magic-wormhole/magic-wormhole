from __future__ import print_function
import sys, json
from twisted.python import log
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
        print("code is:", code)
        return w.send_data(data)
    d.addCallback(_got_code)
    def _sent(_):
        return w.get_data()
    d.addCallback(_sent)
    def _got_data(them_bytes):
        them_d = json.loads(them_bytes.decode("utf-8"))
        if them_d["message"] == "ok":
            print("text sent")
        else:
            print("error sending text: %r" % (them_d,))
    d.addCallback(_got_data)
elif sys.argv[1] == "receive-text":
    code = sys.argv[2]
    w.set_code(code)
    d = w.get_data()
    def _got_data(them_bytes):
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            print("ERROR: " + them_d["error"], file=sys.stderr)
            return 1
        print(them_d["message"])
        data = json.dumps({"message": "ok"}).encode("utf-8")
        return w.send_data(data)
    d.addCallback(_got_data)
else:
    raise ValueError("bad command")
d.addCallback(w.close)
d.addCallback(lambda _: reactor.stop())
d.addErrback(log.err)
reactor.run()
