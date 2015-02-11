import os, sys, json
from binascii import hexlify
from nacl.secret import SecretBox
from nacl import utils
from . import api

APPID = "lothar.com/wormhole/file-xfer"
RELAY = "example.com"

# we're sending
filename = sys.argv[1]
assert os.path.isfile(filename)
xfer_key = utils.random(SecretBox.KEY_SIZE)
blob = json.dumps({"xfer_key": hexlify(xfer_key),
                   "filename": os.path.basename(filename),
                   "filesize": os.stat(filename).st_size,
                   "relay": RELAY,
                   }).encode("utf-8")
i = api.Initiator(APPID, blob)
code = i.start()
print("Wormhole code is '%s'" % code)
print("On the other computer, please run:")
print()
print(" wormhole-receive-file %s" % code)
print()
them_bytes = i.finish()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))

box = SecretBox(xfer_key)
with open(filename, "rb") as f:
    plaintext = f.read()
nonce = utils.random(SecretBox.NONCE_SIZE)
encrypted = box.encrypt(plaintext, nonce)

# now draw the rest of the owl
SEND(RELAY, encrypted)
print("file sent")
