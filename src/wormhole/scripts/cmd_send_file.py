from __future__ import print_function
import os, sys, json, binascii
from ..errors import handle_server_error

APPID = "lothar.com/wormhole/file-xfer"

@handle_server_error
def send_file(args):
    # we're sending
    from ..blocking.transcribe import Initiator, WrongPasswordError
    from ..blocking.transit import TransitSender
    from .progress import start_progress, update_progress, finish_progress

    filename = args.filename
    assert os.path.isfile(filename)
    transit_sender = TransitSender(args.transit_helper)

    i = Initiator(APPID, args.relay_url)
    if args.zeromode:
        assert not args.code
        args.code = "0-"
    if args.code:
        i.set_code(args.code)
        code = args.code
    else:
        code = i.get_code(args.code_length)
    other_cmd = "wormhole receive-file"
    if args.verify:
        other_cmd = "wormhole --verify receive-file"
    if args.zeromode:
        other_cmd += " -0"
    print("On the other computer, please run: %s" % other_cmd)
    if not args.zeromode:
        print("Wormhole code is '%s'" % code)
    print()

    if args.verify:
        verifier = binascii.hexlify(i.get_verifier())
        while True:
            ok = raw_input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                print("verification rejected, abandoning transfer",
                      file=sys.stderr)
                reject_data = json.dumps({"error": "verification rejected",
                                          }).encode("utf-8")
                i.get_data(reject_data)
                return 1

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

    try:
        them_bytes = i.get_data(data)
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    #print("them: %r" % (them_d,))


    tdata = them_d["transit"]
    transit_key = i.derive_key(APPID+"/transit-key")
    transit_sender.set_transit_key(transit_key)
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_sender.connect()

    print("Sending (%s).." % transit_sender.describe())

    CHUNKSIZE = 64*1024
    with open(filename, "rb") as f:
        sent = 0
        next_update = start_progress(filesize)
        while sent < filesize:
            plaintext = f.read(CHUNKSIZE)
            record_pipe.send_record(plaintext)
            sent += len(plaintext)
            next_update = update_progress(next_update, sent, filesize)
        finish_progress(filesize)

    print("File sent.. waiting for confirmation")
    ack = record_pipe.receive_record()
    if ack == "ok\n":
        print("Confirmation received. Transfer complete.")
        return 0
    else:
        print("Transfer failed (remote says: %r)" % ack)
        return 1
