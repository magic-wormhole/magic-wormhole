
import sys, json
from nacl.secret import SecretBox
from nacl import utils
from . import api

APPID = "lothar.com/wormhole/text-xfer"
RELAY = "example.com"

# we're sending
message = sys.argv[1]
xfer_key = utils.random(SecretBox.KEY_SIZE)
blob = json.dumps({"message": message,
                   }).encode("utf-8")
i = api.Initiator(APPID, blob)
code = i.start()
print("Wormhole code is '%s'" % code)
print("On the other computer, please run:")
print()
print(" wormhole-receive-text %s" % code)
print()
them_bytes = i.finish()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))
