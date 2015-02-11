from __future__ import print_function
import sys, json
from wormhole.transcribe import Initiator

APPID = "lothar.com/wormhole/text-xfer"

# we're sending
message = sys.argv[1]
blob = json.dumps({"message": message,
                   }).encode("utf-8")
i = Initiator(APPID, blob)
code = i.get_code()
print("Wormhole code is '%s'" % code)
print("On the other computer, please run:")
print("")
print(" wormhole-receive-text %s" % code)
print("")
them_bytes = i.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))
