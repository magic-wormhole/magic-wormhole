from __future__ import print_function
import time, sys, json
from wormhole.transcribe import Receiver

APPID = "lothar.com/wormhole/text-xfer"

# we're receiving
start = time.time()
code = sys.argv[1]
blob = b"{}"
r = Receiver(APPID, blob, code)
them_bytes = r.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print(them_d["message"])
print("elapsed time: %.2f" % (time.time() - start))
