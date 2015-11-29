from __future__ import print_function
import os, sys, json, binascii, six, tempfile, zipfile
from ..errors import handle_server_error

APPID = u"lothar.com/wormhole/text-or-file-xfer"

def accept_file(args, them_d, w):
    from ..blocking.transit import TransitReceiver, TransitError
    from .progress import start_progress, update_progress, finish_progress

    file_data = them_d["file"]
    if args.output_file:
        filename = args.output_file
    else:
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        filename = os.path.basename(file_data["filename"]) # unicode
    filesize = file_data["filesize"]

    # get confirmation from the user before writing to the local directory
    if os.path.exists(filename):
        print("Error: refusing to overwrite existing file %s" % (filename,))
        data = json.dumps({"error": "file already exists"}).encode("utf-8")
        w.send_data(data)
        return 1

    print("Receiving file (%d bytes) into: %s" % (filesize, filename))
    while True and not args.accept_file:
        ok = six.moves.input("ok? (y/n): ")
        if ok.lower().startswith("y"):
            break
        print("transfer rejected", file=sys.stderr)
        data = json.dumps({"error": "transfer rejected"}).encode("utf-8")
        w.send_data(data)
        return 1

    transit_receiver = TransitReceiver(args.transit_helper)
    data = json.dumps({
        "file_ack": "ok",
        "transit": {
            "direct_connection_hints": transit_receiver.get_direct_hints(),
            "relay_connection_hints": transit_receiver.get_relay_hints(),
            },
        }).encode("utf-8")
    w.send_data(data)
    # now done with the Wormhole object

    # now receive the rest of the owl
    tdata = them_d["transit"]
    transit_key = w.derive_key(APPID+u"/transit-key")
    transit_receiver.set_transit_key(transit_key)
    transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_receiver.connect()

    print("Receiving %d bytes for '%s' (%s).." % (filesize, filename,
                                                  transit_receiver.describe()))
    tmp = filename + ".tmp"
    with open(tmp, "wb") as f:
        received = 0
        next_update = start_progress(filesize)
        while received < filesize:
            try:
                plaintext = record_pipe.receive_record()
            except TransitError:
                print()
                print("Connection dropped before full file received")
                print("got %d bytes, wanted %d" % (received, filesize))
                return 1
            f.write(plaintext)
            received += len(plaintext)
            next_update = update_progress(next_update, received, filesize)
        finish_progress(filesize)
        assert received == filesize

    os.rename(tmp, filename)

    print("Received file written to %s" % filename)
    record_pipe.send_record(b"ok\n")
    record_pipe.close()
    return 0

def accept_directory(args, them_d, w):
    from ..blocking.transit import TransitReceiver, TransitError
    from .progress import start_progress, update_progress, finish_progress

    file_data = them_d["directory"]
    mode = file_data["mode"]
    if mode != "zipfile/deflated":
        print("Error: unknown directory-transfer mode '%s'" % (mode,))
        data = json.dumps({"error": "unknown mode"}).encode("utf-8")
        w.send_data(data)
        return 1

    if args.output_file:
        dirname = args.output_file
    else:
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        dirname = os.path.basename(file_data["dirname"]) # unicode
    filesize = file_data["zipsize"]
    num_files = file_data["numfiles"]
    num_bytes = file_data["numbytes"]

    if os.path.exists(dirname):
        print("Error: refusing to overwrite existing directory %s" % (dirname,))
        data = json.dumps({"error": "directory already exists"}).encode("utf-8")
        w.send_data(data)
        return 1

    print("Receiving directory into: %s/" % (dirname,))
    print("%d files, %d bytes (%d compressed)" % (num_files, num_bytes,
                                                  filesize))
    while True and not args.accept_file:
        ok = six.moves.input("ok? (y/n): ")
        if ok.lower().startswith("y"):
            break
        print("transfer rejected", file=sys.stderr)
        data = json.dumps({"error": "transfer rejected"}).encode("utf-8")
        w.send_data(data)
        return 1

    transit_receiver = TransitReceiver(args.transit_helper)
    data = json.dumps({
        "file_ack": "ok",
        "transit": {
            "direct_connection_hints": transit_receiver.get_direct_hints(),
            "relay_connection_hints": transit_receiver.get_relay_hints(),
            },
        }).encode("utf-8")
    w.send_data(data)
    # now done with the Wormhole object

    # now receive the rest of the owl
    tdata = them_d["transit"]
    transit_key = w.derive_key(APPID+u"/transit-key")
    transit_receiver.set_transit_key(transit_key)
    transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_receiver.connect()

    print("Receiving %d bytes for '%s' (%s).." % (filesize, dirname,
                                                  transit_receiver.describe()))
    f = tempfile.SpooledTemporaryFile()
    received = 0
    next_update = start_progress(filesize)
    while received < filesize:
        try:
            plaintext = record_pipe.receive_record()
        except TransitError:
            print()
            print("Connection dropped before full file received")
            print("got %d bytes, wanted %d" % (received, filesize))
            return 1
        f.write(plaintext)
        received += len(plaintext)
        next_update = update_progress(next_update, received, filesize)
    finish_progress(filesize)
    assert received == filesize
    print("Unpacking zipfile..")
    with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
        zf.extractall(path=dirname)
        # extractall() appears to offer some protection against malicious
        # pathnames. For example, "/tmp/oops" and "../tmp/oops" both do the
        # same thing as the (safe) "tmp/oops".

    print("Received files written to %s/" % dirname)
    record_pipe.send_record(b"ok\n")
    record_pipe.close()
    return 0

@handle_server_error
def receive(args):
    # we're receiving text, or a file
    from ..blocking.transcribe import Wormhole, WrongPasswordError
    assert isinstance(args.relay_url, type(u""))

    with Wormhole(APPID, args.relay_url) as w:
        if args.zeromode:
            assert not args.code
            args.code = u"0-"
        code = args.code
        if not code:
            code = w.input_code("Enter receive wormhole code: ", args.code_length)
        w.set_code(code)

        if args.verify:
            verifier = binascii.hexlify(w.get_verifier()).decode("ascii")
            print("Verifier %s." % verifier)

        try:
            them_bytes = w.get_data()
        except WrongPasswordError as e:
            print("ERROR: " + e.explain(), file=sys.stderr)
            return 1
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            print("ERROR: " + them_d["error"], file=sys.stderr)
            return 1

        if "message" in them_d:
            # we're receiving a text message
            print(them_d["message"])
            data = json.dumps({"message_ack": "ok"}).encode("utf-8")
            w.send_data(data)
            return 0

        if "error" in them_d:
            print("ERROR: " + data["error"], file=sys.stderr)
            return 1

        if "file" in them_d:
            return accept_file(args, them_d, w)

        if "directory" in them_d:
            return accept_directory(args, them_d, w)

        print("I don't know what they're offering\n")
        print("Offer details:", them_d)
        data = json.dumps({"error": "unknown offer type"}).encode("utf-8")
        w.send_data(data)
        return 1
