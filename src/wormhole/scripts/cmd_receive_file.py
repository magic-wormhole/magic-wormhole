from __future__ import print_function
import sys, os, json
from nacl.secret import SecretBox
from wormhole.blocking.transcribe import Receiver, WrongPasswordError
from wormhole.blocking.transit import TransitReceiver

APPID = "lothar.com/wormhole/file-xfer"

def receive_file(so):
    # we're receiving
    transit_receiver = TransitReceiver()

    mydata = json.dumps({
        "transit": {
            "direct_connection_hints": transit_receiver.get_direct_hints(),
            "relay_connection_hints": transit_receiver.get_relay_hints(),
            },
        }).encode("utf-8")
    r = Receiver(APPID, mydata)
    r.set_code(r.input_code("Enter receive-file wormhole code: "))

    try:
        data = json.loads(r.get_data().decode("utf-8"))
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    #print("their data: %r" % (data,))

    file_data = data["file"]
    xfer_key = r.derive_key(APPID+"/xfer-key", SecretBox.KEY_SIZE)
    filename = os.path.basename(file_data["filename"]) # unicode
    filesize = file_data["filesize"]
    encrypted_filesize = filesize + SecretBox.NONCE_SIZE+16

    # now receive the rest of the owl
    tdata = data["transit"]
    transit_key = r.derive_key(APPID+"/transit-key")
    transit_receiver.set_transit_key(transit_key)
    transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
    skt = transit_receiver.establish_connection()
    print("Receiving %d bytes.." % filesize)
    encrypted = b""
    while len(encrypted) < encrypted_filesize:
        more = skt.recv(encrypted_filesize - len(encrypted))
        if not more:
            print("Connection dropped before full file received")
            print("got %d bytes, wanted %d" % (len(encrypted), encrypted_filesize))
            return 1
        encrypted += more
    assert len(encrypted) == encrypted_filesize

    decrypted = SecretBox(xfer_key).decrypt(encrypted)

    # only write to the current directory, and never overwrite anything
    here = os.path.abspath(os.getcwd())
    target = os.path.abspath(os.path.join(here, filename))
    if os.path.dirname(target) != here:
        print("Error: suggested filename (%s) would be outside current directory"
              % (filename,))
        skt.send("bad filename\n")
        skt.close()
        return 1
    if os.path.exists(target):
        print("Error: refusing to overwrite existing file %s" % (filename,))
        skt.send("file already exists\n")
        skt.close()
        return 1
    with open(target, "wb") as f:
        f.write(decrypted)
    print("Received file written to %s" % filename)
    skt.send("ok\n")
    skt.close()
    return 0
