from __future__ import print_function
import os, sys, json, binascii, six, tempfile, zipfile
from ..errors import handle_server_error
from .progress import ProgressPrinter

APPID = u"lothar.com/wormhole/text-or-file-xfer"

def accept_file(args, them_d, w):
    from ..blocking.transit import TransitReceiver, TransitError

    file_data = them_d["file"]
    if args.output_file:
        filename = args.output_file
    else:
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        filename = os.path.basename(file_data["filename"]) # unicode
    abs_filename = os.path.join(args.cwd, filename)
    filesize = file_data["filesize"]

    # get confirmation from the user before writing to the local directory
    if os.path.exists(abs_filename):
        print(u"Error: refusing to overwrite existing file %s" % (filename,),
              file=args.stdout)
        data = json.dumps({"error": "file already exists"}).encode("utf-8")
        w.send_data(data)
        return 1

    print(u"Receiving file (%d bytes) into: %s" % (filesize, filename),
          file=args.stdout)
    while True and not args.accept_file:
        ok = six.moves.input("ok? (y/n): ")
        if ok.lower().startswith("y"):
            break
        print(u"transfer rejected", file=sys.stderr)
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

    print(u"Receiving %d bytes for '%s' (%s).." %
          (filesize, filename, transit_receiver.describe()), file=args.stdout)
    tmp = abs_filename + ".tmp"
    with open(tmp, "wb") as f:
        received = 0
        p = ProgressPrinter(filesize, args.stdout)
        if not args.hide_progress:
            p.start()
        while received < filesize:
            try:
                plaintext = record_pipe.receive_record()
            except TransitError:
                print(u"", file=args.stdout)
                print(u"Connection dropped before full file received",
                      file=args.stdout)
                print(u"got %d bytes, wanted %d" % (received, filesize),
                      file=args.stdout)
                return 1
            f.write(plaintext)
            received += len(plaintext)
            if not args.hide_progress:
                p.update(received)
        if not args.hide_progress:
            p.finish()
        assert received == filesize

    os.rename(tmp, abs_filename)

    print(u"Received file written to %s" % filename, file=args.stdout)
    record_pipe.send_record(b"ok\n")
    record_pipe.close()
    return 0

def accept_directory(args, them_d, w):
    from ..blocking.transit import TransitReceiver, TransitError

    file_data = them_d["directory"]
    mode = file_data["mode"]
    if mode != "zipfile/deflated":
        print(u"Error: unknown directory-transfer mode '%s'" % (mode,),
              file=args.stdout)
        data = json.dumps({"error": "unknown mode"}).encode("utf-8")
        w.send_data(data)
        return 1

    if args.output_file:
        dirname = args.output_file
    else:
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        dirname = os.path.basename(file_data["dirname"]) # unicode
    abs_dirname = os.path.join(args.cwd, dirname)
    filesize = file_data["zipsize"]
    num_files = file_data["numfiles"]
    num_bytes = file_data["numbytes"]

    if os.path.exists(abs_dirname):
        print(u"Error: refusing to overwrite existing directory %s" %
              (dirname,), file=args.stdout)
        data = json.dumps({"error": "directory already exists"}).encode("utf-8")
        w.send_data(data)
        return 1

    print(u"Receiving directory into: %s/" % (dirname,), file=args.stdout)
    print(u"%d files, %d bytes (%d compressed)" %
          (num_files, num_bytes, filesize), file=args.stdout)
    while True and not args.accept_file:
        ok = six.moves.input("ok? (y/n): ")
        if ok.lower().startswith("y"):
            break
        print(u"transfer rejected", file=sys.stderr)
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

    print(u"Receiving %d bytes for '%s' (%s).." %
          (filesize, dirname, transit_receiver.describe()), file=args.stdout)
    f = tempfile.SpooledTemporaryFile()
    received = 0
    p = ProgressPrinter(filesize, args.stdout)
    if not args.hide_progress:
        p.start()
    while received < filesize:
        try:
            plaintext = record_pipe.receive_record()
        except TransitError:
            print(u"", file=args.stdout)
            print(u"Connection dropped before full file received",
                  file=args.stdout)
            print(u"got %d bytes, wanted %d" % (received, filesize),
                  file=args.stdout)
            return 1
        f.write(plaintext)
        received += len(plaintext)
        if not args.hide_progress:
            p.update(received)
    if not args.hide_progress:
        p.finish()
    assert received == filesize
    print(u"Unpacking zipfile..", file=args.stdout)
    with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
        zf.extractall(path=abs_dirname)
        # extractall() appears to offer some protection against malicious
        # pathnames. For example, "/tmp/oops" and "../tmp/oops" both do the
        # same thing as the (safe) "tmp/oops".

    print(u"Received files written to %s/" % dirname, file=args.stdout)
    record_pipe.send_record(b"ok\n")
    record_pipe.close()
    return 0

@handle_server_error
def receive_blocking(args):
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
            print(u"Verifier %s." % verifier, file=args.stdout)

        try:
            them_bytes = w.get_data()
        except WrongPasswordError as e:
            print(u"ERROR: " + e.explain(), file=sys.stderr)
            return 1
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            print(u"ERROR: " + them_d["error"], file=sys.stderr)
            return 1

        if "message" in them_d:
            # we're receiving a text message
            print(them_d["message"], file=args.stdout)
            data = json.dumps({"message_ack": "ok"}).encode("utf-8")
            w.send_data(data)
            return 0

        if "error" in them_d:
            print(u"ERROR: " + data["error"], file=sys.stderr)
            return 1

        if "file" in them_d:
            return accept_file(args, them_d, w)

        if "directory" in them_d:
            return accept_directory(args, them_d, w)

        print(u"I don't know what they're offering\n", file=args.stdout)
        print(u"Offer details:", them_d, file=args.stdout)
        data = json.dumps({"error": "unknown offer type"}).encode("utf-8")
        w.send_data(data)
        return 1
