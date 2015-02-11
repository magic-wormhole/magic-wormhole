
import sys, json
from binascii import unhexlify
from nacl.secret import SecretBox
from nacl import utils
from . import api

APPID = "lothar.com/wormhole/file-xfer"
RELAY = "example.com"

# we're receiving
code = sys.argv[1]
blob = b""
r = api.Receiver(APPID, blob, code)
them_bytes = r.finish()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))
xfer_key = unhexlify(them_d["xfer_key"].encode("ascii"))
filename = them_d["filename"] # unicode
filesize = them_d["filesize"]
relay = them_d["relay"].encode("ascii")

# now receive the rest of the owl
