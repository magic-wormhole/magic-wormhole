from __future__ import print_function
import sys, json
from wormhole.transcribe import Receiver

APPID = "lothar.com/wormhole/text-xfer"

# we're receiving
code = sys.argv[1]
blob = b"{}"
r = Receiver(APPID, blob, code)
them_bytes = r.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print(them_d["message"])
