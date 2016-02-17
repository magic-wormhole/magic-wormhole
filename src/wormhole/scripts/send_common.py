from __future__ import print_function
import os, sys, six, tempfile, zipfile
from ..errors import TransferError

APPID = u"lothar.com/wormhole/text-or-file-xfer"

def handle_zero(args):
    if args.zeromode:
        assert not args.code
        args.code = u"0-"

def build_other_command(args):
    other_cmd = "wormhole receive"
    if args.verify:
        other_cmd = "wormhole --verify receive"
    if args.zeromode:
        other_cmd += " -0"
    return other_cmd

def build_phase1_data(args):
    phase1 = {}

    text = args.text
    if text == "-":
        print(u"Reading text message from stdin..", file=args.stdout)
        text = sys.stdin.read()
    if not text and not args.what:
        text = six.moves.input("Text to send: ")

    if text is not None:
        print(u"Sending text message (%d bytes)" % len(text), file=args.stdout)
        phase1 = { "message": text }
        fd_to_send = None
        return phase1, fd_to_send

    what = os.path.join(args.cwd, args.what)
    if not os.path.exists(what):
        raise TransferError("Cannot send: no file/directory named '%s'" %
                            args.what)
    basename = os.path.basename(what)

    if os.path.isfile(what):
        # we're sending a file
        filesize = os.stat(what).st_size
        phase1["file"] = {
            "filename": basename,
            "filesize": filesize,
            }
        print(u"Sending %d byte file named '%s'" % (filesize, basename),
              file=args.stdout)
        fd_to_send = open(what, "rb")
        return phase1, fd_to_send

    if os.path.isdir(what):
        print(u"Building zipfile..", file=args.stdout)
        # We're sending a directory. Create a zipfile in a tempdir and
        # send that.
        fd_to_send = tempfile.SpooledTemporaryFile()
        # TODO: I think ZIP_DEFLATED means compressed.. check it
        num_files = 0
        num_bytes = 0
        tostrip = len(what.split(os.sep))
        with zipfile.ZipFile(fd_to_send, "w", zipfile.ZIP_DEFLATED) as zf:
            for path,dirs,files in os.walk(what):
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
        print(u"Sending directory (%d bytes compressed) named '%s'"
              % (filesize, basename), file=args.stdout)
        return phase1, fd_to_send

    raise TypeError("'%s' is neither file nor directory" % args.what)
