from __future__ import print_function
import os, sys, json, binascii, six, tempfile, zipfile
from ..errors import handle_server_error

APPID = u"lothar.com/wormhole/text-or-file-xfer"

@handle_server_error
def send(args):
    # we're sending text, or a file/directory
    from ..blocking.transcribe import Wormhole, WrongPasswordError
    from ..blocking.transit import TransitSender
    from .progress import start_progress, update_progress, finish_progress
    assert isinstance(args.relay_url, type(u""))

    text = args.text
    if text == "-":
        print("Reading text message from stdin..")
        text = sys.stdin.read()
    if not text and not args.what:
        text = six.moves.input("Text to send: ")

    if text is not None:
        sending_message = True
        print("Sending text message (%d bytes)" % len(text))
        phase1 = {
            "message": text,
            }
    else:
        if not os.path.exists(args.what):
            print("Cannot send: no file/directory named '%s'" % args.what)
            return 1
        sending_message = False
        transit_sender = TransitSender(args.transit_helper)
        phase1 = {
            "transit": {
                "direct_connection_hints": transit_sender.get_direct_hints(),
                "relay_connection_hints": transit_sender.get_relay_hints(),
                },
            }
        basename = os.path.basename(args.what)
        if os.path.isfile(args.what):
            # we're sending a file
            filesize = os.stat(args.what).st_size
            phase1["file"] = {
                "filename": basename,
                "filesize": filesize,
                }
            print("Sending %d byte file named '%s'" % (filesize, basename))
            fd_to_send = open(args.what, "rb")
        elif os.path.isdir(args.what):
            print("Building zipfile..")
            # We're sending a directory. Create a zipfile in a tempdir and
            # send that.
            fd_to_send = tempfile.SpooledTemporaryFile()
            # TODO: I think ZIP_DEFLATED means compressed.. check it
            num_files = 0
            num_bytes = 0
            tostrip = len(args.what.split(os.sep))
            with zipfile.ZipFile(fd_to_send, "w", zipfile.ZIP_DEFLATED) as zf:
                for path,dirs,files in os.walk(args.what):
                    # path always starts with args.what, then sometimes might
                    # have "/subdir" appended. We want the zipfile to contain
                    # "" or "subdir"
                    localpath = list(path.split(os.sep)[tostrip:])
                    for fn in files:
                        archivename = os.path.join(*tuple(localpath+[fn]))
                        localfilename = os.path.join(path, fn)
                        zf.write(localfilename, archivename)
                        num_bytes += os.stat(localfilename).st_size
                        num_files += 1
            fd_to_send.seek(0,2)
            filesize = fd_to_send.tell()
            fd_to_send.seek(0,0)
            phase1["directory"] = {
                "mode": "zipfile/deflated",
                "dirname": basename,
                "zipsize": filesize,
                "numbytes": num_bytes,
                "numfiles": num_files,
                }
            print("Sending directory (%d bytes compressed) named '%s'"
                  % (filesize, basename))

    with Wormhole(APPID, args.relay_url) as w:
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
                    return 1

        my_phase1_bytes = json.dumps(phase1).encode("utf-8")
        w.send_data(my_phase1_bytes)
        try:
            them_phase1_bytes = w.get_data()
        except WrongPasswordError as e:
            print("ERROR: " + e.explain(), file=sys.stderr)
            return 1
        them_phase1 = json.loads(them_phase1_bytes.decode("utf-8"))

        if sending_message:
            if them_phase1["message_ack"] == "ok":
                print("text message sent")
                return 0
            print("error sending text: %r" % (them_phase1,))
            return 1

        if "error" in them_phase1:
            print("remote error: %s" % them_phase1["error"])
            print("transfer abandoned")
            return 1
        if them_phase1.get("file_ack") != "ok":
            print("ambiguous response from remote: %s" % (them_phase1,))
            print("transfer abandoned")
            return 1

    tdata = them_phase1["transit"]
    transit_key = w.derive_key(APPID+"/transit-key")
    transit_sender.set_transit_key(transit_key)
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_sender.connect()

    print("Sending (%s).." % transit_sender.describe())

    CHUNKSIZE = 64*1024
    with fd_to_send as f:
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

