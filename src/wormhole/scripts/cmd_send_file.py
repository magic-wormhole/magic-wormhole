from __future__ import print_function
import os, sys, json
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Initiator, WrongPasswordError
from wormhole.blocking.transit import TransitSender
from .progress import start_progress, update_progress, finish_progress

APPID = "lothar.com/wormhole/file-xfer"

def send_file(so):
    # we're sending
    filename = so["filename"]
    assert os.path.isfile(filename)
    transit_sender = TransitSender()

    filesize = os.stat(filename).st_size
    data = json.dumps({
        "file": {
            "filename": os.path.basename(filename),
            "filesize": filesize,
            },
        "transit": {
            "direct_connection_hints": transit_sender.get_direct_hints(),
            "relay_connection_hints": transit_sender.get_relay_hints(),
            },
        }).encode("utf-8")

    i = Initiator(APPID, data)
    code = i.get_code()
    print("On the other computer, please run: receive_file")
    print("Wormhole code is '%s'" % code)
    print("")
    try:
        them_bytes = i.get_data()
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    #print("them: %r" % (them_d,))
    xfer_key = i.derive_key(APPID+"/xfer-key", SecretBox.KEY_SIZE)

    print("Encrypting %d bytes.." % filesize)

    box = SecretBox(xfer_key)
    with open(filename, "rb") as f:
        plaintext = f.read()
    nonce = os.urandom(SecretBox.NONCE_SIZE)
    encrypted = box.encrypt(plaintext, nonce)

    tdata = them_d["transit"]
    transit_key = i.derive_key(APPID+"/transit-key")
    transit_sender.set_transit_key(transit_key)
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    skt = transit_sender.establish_connection()

    print("Sending (%s).." % transit_sender.describe())

    sent = 0
    next_update = start_progress(len(encrypted))
    while sent < len(encrypted):
        sent += skt.send(encrypted[sent:])
        next_update = update_progress(next_update, sent, len(encrypted))
    finish_progress(len(encrypted))

    print("File sent.. waiting for confirmation")
    # ack is a short newline-terminated string, followed by socket close. A long
    # read is probably good enough.
    ack = skt.recv(300)
    if ack == "ok\n":
        print("Confirmation received. Transfer complete.")
        return 0
    else:
        print("Transfer failed (remote says: %r)" % ack)
        return 1
