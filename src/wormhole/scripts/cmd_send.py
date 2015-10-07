from __future__ import print_function
import os, sys, json, binascii, six
from ..errors import handle_server_error

APPID = u"lothar.com/wormhole/text-or-file-xfer"

@handle_server_error
def send(args):
    # we're sending text, or a file
    from ..blocking.transcribe import Wormhole, WrongPasswordError
    from ..blocking.transit import TransitSender
    from .progress import start_progress, update_progress, finish_progress
    assert isinstance(args.relay_url, type(u""))

    text = args.text
    if not text and not args.what:
        text = six.moves.input("Text to send: ")

    if text is not None:
        sending_message = True
        print("Sending text message (%d bytes)" % len(text))
        phase1 = {
            "message": text,
            }
    else:
        if not os.path.isfile(args.what):
            print("Cannot send: no file named '%s'" % args.what)
            return 1
        # we're sending a file
        sending_message = False
        filesize = os.stat(args.what).st_size
        basename = os.path.basename(args.what)
        print("Sending %d byte file named '%s'" % (filesize, basename))
        transit_sender = TransitSender(args.transit_helper)
        phase1 = {
            "file": {
                "filename": basename,
                "filesize": filesize,
                },
            "transit": {
                "direct_connection_hints": transit_sender.get_direct_hints(),
                "relay_connection_hints": transit_sender.get_relay_hints(),
                },
            }

    w = Wormhole(APPID, args.relay_url)
    if args.zeromode:
        assert not args.code
        args.code = u"0-"
    if args.code:
        w.set_code(args.code)
        code = args.code
    else:
        code = w.get_code(args.code_length)
    other_cmd = "wormhole receive"
    if args.verify:
        other_cmd = "wormhole --verify receive"
    if args.zeromode:
        other_cmd += " -0"
    print("On the other computer, please run: %s" % other_cmd)
    if not args.zeromode:
        print("Wormhole code is: %s" % code)
    print("")

    if args.verify:
        verifier = binascii.hexlify(w.get_verifier()).decode("ascii")
        while True:
            ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                print("verification rejected, abandoning transfer",
                      file=sys.stderr)
                reject_data = json.dumps({"error": "verification rejected",
                                          }).encode("utf-8")
                w.send_data(reject_data)
                w.close()
                return 1

    my_phase1_bytes = json.dumps(phase1).encode("utf-8")
    w.send_data(my_phase1_bytes)
    try:
        them_phase1_bytes = w.get_data()
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        w.close()
        return 1
    them_phase1 = json.loads(them_phase1_bytes.decode("utf-8"))

    if sending_message:
        if them_phase1["message_ack"] == "ok":
            print("text message sent")
            w.close()
            return 0
        print("error sending text: %r" % (them_phase1,))
        w.close()
        return 1

    if "error" in them_phase1:
        print("remote error: %s" % them_phase1["error"])
        print("transfer abandoned")
        w.close()
        return 1
    if them_phase1.get("file_ack") != "ok":
        print("ambiguous response from remote: %s" % (them_phase1,))
        print("transfer abandoned")
        w.close()
        return 1
    w.close()

    tdata = them_phase1["transit"]
    transit_key = w.derive_key(APPID+"/transit-key")
    transit_sender.set_transit_key(transit_key)
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_sender.connect()

    print("Sending (%s).." % transit_sender.describe())

    CHUNKSIZE = 64*1024
    with open(args.what, "rb") as f:
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
    if ack == b"ok\n":
        print("Confirmation received. Transfer complete.")
        return 0
    print("Transfer failed (remote says: %r)" % ack)
    return 1

