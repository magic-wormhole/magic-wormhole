from __future__ import print_function
import os, sys, json
from binascii import unhexlify
from nacl.secret import SecretBox
from .transcribe import Receiver

APPID = "lothar.com/wormhole/file-xfer"
RELAY = "example.com"

# we're receiving
code = sys.argv[1]
blob = b""
r = Receiver(APPID, blob, code)
them_bytes = r.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))
xfer_key = unhexlify(them_d["xfer_key"].encode("ascii"))
filename = os.path.basename(them_d["filename"]) # unicode
filesize = them_d["filesize"]
relay = them_d["relay"].encode("ascii")

# now receive the rest of the owl
encrypted = RECEIVE(relay)

decrypted = SecretBox(xfer_key).decrypt(encrypted)

# only write to the current directory, and never overwrite anything
here = os.path.abspath(os.getcwd())
target = os.path.abspath(os.path.join(here, filename))
assert os.path.dirname(target) == here
assert not os.path.exists(target)
with open(target, "wb") as f:
    f.write(decrypted)
print("%s written" % filename)
