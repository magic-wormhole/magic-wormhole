import sys
from twisted.internet import reactor
from .transcribe import SymmetricWormhole
from .. import public_relay

APPID = "lothar.com/wormhole/text-xfer"

w = SymmetricWormhole(APPID, public_relay.RENDEZVOUS_RELAY)

if sys.argv[1] == "send-text":
    message = sys.argv[2]
    d = w.get_code()
    def _got_code(code):
        print "code is:", code
        return w.get_data(message)
    d.addCallback(_got_code)
    def _got_data(their_data):
        print "ack:", their_data
    d.addCallback(_got_data)
elif sys.argv[1] == "receive-text":
    code = sys.argv[2]
    w.set_code(code)
    d = w.get_data("ok")
    def _got_data(their_data):
        print their_data
    d.addCallback(_got_data)
else:
    raise ValueError("bad command")
d.addCallback(lambda _: reactor.stop())
reactor.run()
