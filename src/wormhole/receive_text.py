import sys, json
from . import api

APPID = "lothar.com/wormhole/text-xfer"

# we're receiving
code = sys.argv[1]
blob = b""
r = api.Receiver(APPID, blob, code)
them_bytes = r.finish()
them_d = json.loads(them_bytes.decode("utf-8"))
print(them_d["message"])
