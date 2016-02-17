from __future__ import print_function
import os, sys, six, tempfile, zipfile
from ..errors import handle_server_error, TransferError

APPID = u"lothar.com/wormhole/text-or-file-xfer"

@handle_server_error
def send(args):
    # we're sending text, or a file/directory
    assert isinstance(args.relay_url, type(u""))

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
    else:
        what = os.path.join(args.cwd, args.what)
        if not os.path.exists(what):
            raise TransferError("Cannot send: no file/directory named '%s'" %
                                args.what)
        phase1, fd_to_send = _build_phase1_data(args)
        # transit_sender will be built in twisted/blocking-specific function

    if args.zeromode:
        assert not args.code
        args.code = u"0-"

    other_cmd = "wormhole receive"
    if args.verify:
        other_cmd = "wormhole --verify receive"
    if args.zeromode:
        other_cmd += " -0"
    print(u"On the other computer, please run: %s" % other_cmd,
          file=args.stdout)

    from .cmd_send_blocking import send_blocking
    rc = send_blocking(APPID, args, phase1, fd_to_send)
    return rc

def _build_phase1_data(args):
    phase1 = {}
    what = os.path.join(args.cwd, args.what)
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
    elif os.path.isdir(what):
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
    else:
        raise TypeError("'%s' is neither file nor directory" % what)
    return phase1, fd_to_send
