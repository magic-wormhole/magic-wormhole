from __future__ import print_function
import os, sys, json
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Initiator
from wormhole.blocking.transit import TransitSender

APPID = "lothar.com/wormhole/file-xfer"

# we're sending
filename = sys.argv[1]
assert os.path.isfile(filename)
transit_sender = TransitSender()
direct_hints = transit_sender.get_direct_hints()
relay_hints = transit_sender.get_relay_hints()

filesize = os.stat(filename).st_size
data = json.dumps({
    "file": {
        "filename": os.path.basename(filename),
        "filesize": filesize,
        },
    "transit": {
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
xfer_key = i.derive_key(APPID+"/xfer-key", SecretBox.KEY_SIZE)

box = SecretBox(xfer_key)
with open(filename, "rb") as f:
    plaintext = f.read()
nonce = os.urandom(SecretBox.NONCE_SIZE)
encrypted = box.encrypt(plaintext, nonce)

tdata = them_d["transit"]
transit_key = i.derive_key(APPID+"/transit-key")
transit_sender.set_transit_key(transit_key)
transit_sender.add_receiver_hints(tdata["direct_connection_hints"])
skt = transit_sender.establish_connection()

print("Sending %d bytes.." % filesize)
sent = 0
while sent < len(encrypted):
    more = skt.send(encrypted[sent:])
    sent += more

print("File sent.. waiting for confirmation")
# ack is a short newline-terminated string, followed by socket close. A long
# read is probably good enough.
ack = skt.recv(300)
if ack == "ok\n":
    print("Confirmation received. Transfer complete.")
    sys.exit(0)
else:
    print("Transfer failed (remote says: %r)" % ack)
    sys.exit(1)
