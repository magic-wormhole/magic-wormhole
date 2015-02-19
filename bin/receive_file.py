from __future__ import print_function
import sys, os, json
from binascii import unhexlify
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Receiver
from wormhole.blocking.transit import TransitReceiver

APPID = "lothar.com/wormhole/file-xfer"

# we're receiving
transit_receiver = TransitReceiver()
direct_hints = transit_receiver.get_direct_hints()

mydata = json.dumps({
    "transit": {
        "direct_connection_hints": direct_hints,
        },
    }).encode("utf-8")
r = Receiver(APPID, mydata)
r.set_code(r.input_code("Enter receive-file wormhole code: "))

data = json.loads(r.get_data().decode("utf-8"))
print("their data: %r" % (data,))

file_data = data["file"]
xfer_key = unhexlify(file_data["key"].encode("ascii"))
filename = os.path.basename(file_data["filename"]) # unicode
filesize = file_data["filesize"]
encrypted_filesize = filesize + SecretBox.NONCE_SIZE+16

# now receive the rest of the owl
tdata = data["transit"]
print("calling tr.set_transit_key()")
transit_receiver.set_transit_key(tdata["key"])
transit_receiver.add_sender_direct_hints(tdata["direct_connection_hints"])
transit_receiver.add_sender_relay_hints(tdata["relay_connection_hints"])
skt = transit_receiver.establish_connection()
encrypted = skt.recv(encrypted_filesize)
if len(encrypted) != encrypted_filesize:
    print("Connection dropped before file received")
    sys.exit(1)

decrypted = SecretBox(xfer_key).decrypt(encrypted)

# only write to the current directory, and never overwrite anything
here = os.path.abspath(os.getcwd())
target = os.path.abspath(os.path.join(here, filename))
assert os.path.dirname(target) == here
assert not os.path.exists(target)
with open(target, "wb") as f:
    f.write(decrypted)
print("%s written" % filename)
