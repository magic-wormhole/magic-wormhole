from __future__ import print_function
import os, sys, json
from binascii import hexlify
from nacl.secret import SecretBox
from nacl import utils
from wormhole.blocking.transcribe import Initiator
from wormhole.blocking.transit import TransitSender

APPID = "lothar.com/wormhole/file-xfer"

# we're sending
filename = sys.argv[1]
assert os.path.isfile(filename)
xfer_key = utils.random(SecretBox.KEY_SIZE)
transit_sender = TransitSender()
direct_hints = transit_sender.get_direct_hints()
relay_hints = transit_sender.get_relay_hints()

data = json.dumps({"xfer_key": hexlify(xfer_key),
                   "filename": os.path.basename(filename),
                   "filesize": os.stat(filename).st_size,
                   "direct_connection_hints": direct_hints,
                   "relay_connection_hints": relay_hints,
                   }).encode("utf-8")

i = Initiator(APPID, data)
code = i.get_code()
print("On the other computer, please run: receive_file")
print("Wormhole code is '%s'" % code)
print("")
them_bytes = i.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))

box = SecretBox(xfer_key)
with open(filename, "rb") as f:
    plaintext = f.read()
nonce = utils.random(SecretBox.NONCE_SIZE)
encrypted = box.encrypt(plaintext, nonce)

transit_sender.add_receiver_hints(them_d["direct_connection_hints"])
transit_sender.establish_connection(IDS)
transit_sender.write(encrypted)
transit_sender.close()

print("file sent")
