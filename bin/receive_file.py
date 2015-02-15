from __future__ import print_function
import os, sys, json
from binascii import unhexlify
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Receiver
from wormhole.codes import input_code_with_completion
from wormhole.blocking.transit import TransitReceiver

APPID = "lothar.com/wormhole/file-xfer"

# we're receiving
transit_receiver = TransitReceiver()
direct_hints = transit_receiver.get_direct_hints()

data = json.dumps({"direct_connection_hints": direct_hints,
                   }).encode("utf-8")
r = Receiver(APPID, data)
r.set_code(r.input_code("Enter receive-text wormhole code: "))

them_bytes = r.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print("them: %r" % (them_d,))

xfer_key = unhexlify(them_d["xfer_key"].encode("ascii"))
filename = os.path.basename(them_d["filename"]) # unicode
filesize = them_d["filesize"]

# now receive the rest of the owl
transit_receiver.add_sender_direct_hints(them_d["direct_connection_hints"])
transit_receiver.add_sender_relay_hints(them_d["relay_connection_hints"])
transit_receiver.establish_connection(IDS)
encrypted = transit_receiver.receive()

decrypted = SecretBox(xfer_key).decrypt(encrypted)

# only write to the current directory, and never overwrite anything
here = os.path.abspath(os.getcwd())
target = os.path.abspath(os.path.join(here, filename))
assert os.path.dirname(target) == here
assert not os.path.exists(target)
with open(target, "wb") as f:
    f.write(decrypted)
print("%s written" % filename)
