from __future__ import print_function
import os, sys, json
from binascii import hexlify
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Initiator
from wormhole.blocking.transit import TransitSender

APPID = "lothar.com/wormhole/file-xfer"

# we're sending
filename = sys.argv[1]
assert os.path.isfile(filename)
xfer_key = os.urandom(SecretBox.KEY_SIZE)
transit_sender = TransitSender()
transit_key = transit_sender.get_transit_key()
direct_hints = transit_sender.get_direct_hints()
relay_hints = transit_sender.get_relay_hints()

filesize = os.stat(filename).st_size
data = json.dumps({
    "file": {
        "key": hexlify(xfer_key),
        "filename": os.path.basename(filename),
        "filesize": filesize,
        },
    "transit": {
        "key": hexlify(transit_key),
        "direct_connection_hints": direct_hints,
        "relay_connection_hints": relay_hints,
        },
    }).encode("utf-8")

i = Initiator(APPID, data)
code = i.get_code()
print("On the other computer, please run: receive_file")
print("Wormhole code is '%s'" % code)
print("")
them_bytes = i.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
#print("them: %r" % (them_d,))

box = SecretBox(xfer_key)
with open(filename, "rb") as f:
    plaintext = f.read()
nonce = os.urandom(SecretBox.NONCE_SIZE)
encrypted = box.encrypt(plaintext, nonce)

tdata = them_d["transit"]
transit_sender.add_receiver_hints(tdata["direct_connection_hints"])
skt = transit_sender.establish_connection()

print("Sending %d bytes.." % filesize)
skt.send(encrypted)

print("File sent.. waiting for confirmation")
ack = skt.recv(3)
if ack == "ok\n":
    print("Confirmation received. Transfer complete.")
else:
    print("Transfer failed (remote says: '%r')" % ack)
skt.close()
