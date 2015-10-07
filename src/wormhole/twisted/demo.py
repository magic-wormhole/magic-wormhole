from __future__ import print_function
import sys, json
from twisted.python import log
from twisted.internet import reactor
from .transcribe import Wormhole
from .. import public_relay

APPID = u"lothar.com/wormhole/text-or-file-xfer"
relay_url = public_relay.RENDEZVOUS_RELAY

w = Wormhole(APPID, relay_url)

if sys.argv[1] == "send":
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
        if them_d["message_ack"] == "ok":
            print("text sent")
        else:
            print("error sending text: %r" % (them_d,))
    d.addCallback(_got_data)
elif sys.argv[1] == "receive":
    code = sys.argv[2].decode("utf-8")
    w.set_code(code)
    d = w.get_data()
    def _got_data(them_bytes):
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            print("ERROR: " + them_d["error"], file=sys.stderr)
            raise RuntimeError
        if "file" in them_d:
            print("they're trying to send us a file, which I don't handle")
            data = json.dumps({"error": "not capable of receiving files"})
            d1 = w.send_data(data.encode("utf-8"))
            d1.addCallback(lambda _: RuntimeError())
            return d1
        if not "message" in them_d:
            print("I don't know what they're offering\n")
            print(them_d)
            data = json.dumps({"error": "huh?"})
            d1 = w.send_data(data.encode("utf-8"))
            d1.addCallback(lambda _: RuntimeError())
            return d1
        print(them_d["message"])
        data = json.dumps({"message_ack": "ok"})
        d1 = w.send_data(data.encode("utf-8"))
        d1.addCallback(lambda _: 0)
        return d1
    d.addCallback(_got_data)
else:
    raise ValueError("bad command")

d.addBoth(w.close)
rc = []
def _success(res):
    rc.append(res)
def _fail(f):
    log.err(f)
    rc.append(1)
d.addCallbacks(_success, _fail)
d.addCallback(lambda _: reactor.stop())
reactor.run()
sys.exit(rc[0])
