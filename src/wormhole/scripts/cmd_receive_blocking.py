from __future__ import print_function
import io, os, sys, json, binascii, six, tempfile, zipfile
from ..blocking.transcribe import Wormhole, WrongPasswordError
from ..blocking.transit import TransitReceiver, TransitError
from ..errors import handle_server_error
from .progress import ProgressPrinter

APPID = u"lothar.com/wormhole/text-or-file-xfer"

@handle_server_error
def receive_blocking(args):
    # we're receiving text, or a file
    assert isinstance(args.relay_url, type(u""))

    with Wormhole(APPID, args.relay_url) as w:
        if args.zeromode:
            assert not args.code
            args.code = u"0-"
        code = args.code
        if not code:
            code = w.input_code("Enter receive wormhole code: ",
                                args.code_length)
        w.set_code(code)

        verifier = binascii.hexlify(w.get_verifier()).decode("ascii")
        if args.verify:
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
            mode = "file"
            file_data = them_d["file"]
            # the basename() is intended to protect us against
            # "~/.ssh/authorized_keys" and other attacks
            destname = os.path.basename(file_data["filename"]) # unicode
            xfersize = file_data["filesize"]
        elif "directory" in them_d:
            mode = "directory"
            file_data = them_d["directory"]
            zipmode = file_data["mode"]
            if zipmode != "zipfile/deflated":
                print(u"Error: unknown directory-transfer mode '%s'" %
                      (zipmode,), file=args.stdout)
                data = json.dumps({"error": "unknown mode"}).encode("utf-8")
                w.send_data(data)
                return 1
            destname = os.path.basename(file_data["dirname"]) # unicode
            xfersize = file_data["zipsize"]
            num_files = file_data["numfiles"]
            num_bytes = file_data["numbytes"]
        else:
            print(u"I don't know what they're offering\n", file=args.stdout)
            print(u"Offer details:", them_d, file=args.stdout)
            data = json.dumps({"error": "unknown offer type"}).encode("utf-8")
            w.send_data(data)
            return 1

        if args.output_file:
            destname = args.output_file # override
        abs_destname = os.path.join(args.cwd, destname)

        # get confirmation from the user before writing to the local directory
        if os.path.exists(abs_destname):
            print(u"Error: refusing to overwrite existing %s %s" %
                  (mode, destname), file=args.stdout)
            data = json.dumps({"error": "%s already exists" % mode}).encode("utf-8")
            w.send_data(data)
            return 1
        # TODO: add / to destname
        print(u"Receiving %s (%d bytes) into: %s" % (mode, xfersize, destname),
              file=args.stdout)
        if mode == "directory":
            print(u"%d files, %d bytes (uncompressed)" %
                  (num_files, num_bytes), file=args.stdout)

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
              (xfersize, destname, transit_receiver.describe()),
              file=args.stdout)
        if mode == "file":
            tmp_destname = abs_destname + ".tmp"
            f = open(tmp_destname, "wb")
        else:
            f = tempfile.SpooledTemporaryFile()

        progress_stdout = args.stdout
        if args.hide_progress:
            progress_stdout = io.StringIO()
        received = 0
        p = ProgressPrinter(xfersize, progress_stdout)
        p.start()
        while received < xfersize:
            try:
                plaintext = record_pipe.receive_record()
            except TransitError:
                print(u"", file=args.stdout)
                print(u"Connection dropped before full file received",
                      file=args.stdout)
                print(u"got %d bytes, wanted %d" % (received, xfersize),
                      file=args.stdout)
                return 1
            f.write(plaintext)
            received += len(plaintext)
            p.update(received)
        p.finish()
        assert received == xfersize

        if mode == "file":
            f.close()
            os.rename(tmp_destname, abs_destname)
            print(u"Received file written to %s" % destname, file=args.stdout)
        else:
            print(u"Unpacking zipfile..", file=args.stdout)
            with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
                zf.extractall(path=abs_destname)
                # extractall() appears to offer some protection against
                # malicious pathnames. For example, "/tmp/oops" and
                # "../tmp/oops" both do the same thing as the (safe)
                # "tmp/oops".
            print(u"Received files written to %s/" % destname, file=args.stdout)
            f.close()

        record_pipe.send_record(b"ok\n")
        record_pipe.close()
        return 0

