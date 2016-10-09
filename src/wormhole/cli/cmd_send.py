from __future__ import print_function
import os, sys, six, tempfile, zipfile, hashlib
from tqdm import tqdm
from twisted.python import log
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.threads import deferToThread
from ..errors import TransferError, WormholeClosedError
from ..wormhole import wormhole
from ..transit import TransitSender
from ..util import dict_to_bytes, bytes_to_dict, bytes_to_hexstr
from .verify import verify

APPID = u"lothar.com/wormhole/text-or-file-xfer"

@inlineCallbacks
def send(args, reactor):
    """I implement 'wormhole send'. I return a Deferred that fires with None
    (for success), or signals one of the following errors:
    * WrongPasswordError: the two sides didn't use matching passwords
    * Timeout: something didn't happen fast enough for our tastes
    * TransferError: the receiver rejected the transfer: verifier mismatch,
                     permission not granted, ack not successful.
    * any other error: something unexpected happened
    """
    (w, offer, fd_to_send) = yield _build_wormhole(args, reactor)
    # wormhole is now connected, close it if anything goes wrong. It'd be
    # nice to use 'with w:' here, but that cleanup would be synchronous,
    # and we need it to yield a Deferred.
    try:
        retval = yield _do_send(w, offer, fd_to_send, args, reactor)
    finally:
        yield w.close() # must wait for ack from close()
    returnValue(retval)

@inlineCallbacks
def _build_wormhole(args, reactor):
    assert isinstance(args.relay_url, type(u""))
    timing = args.timing
    stdout = args.stdout

    tor_manager = None
    if args.tor:
        with timing.add("import", which="tor_manager"):
            from ..tor_manager import TorManager
        tor_manager = TorManager(reactor, timing=timing)
        # For now, block everything until Tor has started. Soon: launch
        # tor in parallel with everything else, make sure the TorManager
        # can lazy-provide an endpoint, and overlap the startup process
        # with the user handing off the wormhole code
        yield tor_manager.start()

    other_cmd = "wormhole receive"
    wormhole_args = {"timing": timing}

    # what are we sending?
    text = args.text
    if text == "-":
        print(u"Reading text message from stdin..", file=stdout)
        text = sys.stdin.read()
    if not text and not args.what:
        text = six.moves.input("Text to send: ")

    (offer, fd_to_send) = build_offer(text, args)
    assert fd_to_send != 0 # we use 'if fd_to_send' later

    if args.zeromode:
        assert not args.code
        args.code = u"0-"
        other_cmd += " -0"

    if args.code:
        wormhole_args["code"] = args.code
    else:
        def display_code(code):
            print(u"Wormhole code is: %s" % code, file=stdout)
        maker = CodeMaker(stdin, stdout, display_code)
        wormhole_args["code_maker"] = maker
    print(u"On the other computer, please run: %s" % other_cmd,
          file=stdout)
    print(u"", file=stdout)

    w = yield wormhole(APPID, args.relay_url, reactor,
                       tor_manager=tor_manager, **wormhole_args)
    returnValue((w, offer, fd_to_send))

def _do_send(w, offer, fd_to_send, args, reactor):
    if args.verify:
        # the presence of verifier= may cause WrongPasswordError to be raised
        # earlier than otherwise? it used to be raised in w.verify()
        verifier_bytes = yield w.verify()
        _send_data({"verify": True}, w)
        (res, err) = yield deferToThread(verify, verifier_bytes)
        if not res:
            reject_data = dict_to_bytes({"error": err})
            w.send(reject_data)
            raise TransferError(err) # causes wormhole() to re-raise

    _send_data({"offer": offer}, w)
    # If we're sending text, they'll answer with
    # {answer:{message_ack:ok}}. If we're sending a file and they accept,
    # they'll answer with {answer:{file_ack: ok}}, and then embiggen. If
    # they reject, they'll answer with {error: "transfer rejected"}
    if fd_to_send:
        big_wormhole_d = yield w.embiggen() # start the process early
    response = yield w.read()
    them_d_bytes = yield w.get()
    them_d = bytes_to_dict(them_d_bytes)
    if u"error" in them_d:
        raise TransferError("remote error, transfer abandoned: %s"
                            % them_d["error"])
    answer = them_d[u"answer"]
    if not fd_to_send:
        if them_answer["message_ack"] == "ok":
            print(u"text message sent", file=stdout)
            returnValue(None) # terminates this function
        raise TransferError("error sending text: %r" % (them_answer,))

    if them_answer.get("file_ack") != "ok":
        raise TransferError("ambiguous response from remote, "
                            "transfer abandoned: %s" % (them_answer,))
    big_w = yield big_wormhole_d
    timing.add("transit connected")
    # implements IConsumer

    fd_to_send.seek(0,2)
    filesize = fd_to_send.tell()
    fd_to_send.seek(0,0)

    print(u"Sending (%s).." % big_w.describe(), file=stdout)

    hasher = hashlib.sha256()
    progress = tqdm(file=stdout, disable=args.hide_progress,
                    unit="B", unit_scale=True,
                    total=filesize)
    def _count_and_hash(data):
        hasher.update(data)
        progress.update(len(data))
        return data
    fs = basic.FileSender()

    with timing.add("tx file"):
        with progress:
            yield fs.beginFileTransfer(fd_to_send, big_w,
                                       transform=_count_and_hash)

    expected_hash = hasher.digest()
    expected_hex = bytes_to_hexstr(expected_hash)
    print(u"File sent.. waiting for confirmation", file=stdout)
    with timing.add("get ack") as t:
        ack_bytes = yield big_w.receive_record()
        big_w.close()
        ack = bytes_to_dict(ack_bytes)
        ok = ack.get(u"ack", u"")
        if ok != u"ok":
            t.detail(ack="failed")
            raise TransferError("Transfer failed (remote says: %r)" % ack)
        if u"sha256" in ack:
            if ack[u"sha256"] != expected_hex:
                t.detail(datahash="failed")
                raise TransferError("Transfer failed (bad remote hash)")
        print(u"Confirmation received. Transfer complete.", file=stdout)
        t.detail(ack="ok")

def _send_data(data, w):
    data_bytes = dict_to_bytes(data)
    w.send(data_bytes)

def build_offer():
    # this could be async, but eventually I plan to switch to streaming the
    # zipfile creation (move zf.write into a thread, write it into a
    # socket-to-self, make fd_to_send the read-side of that socket pair).
    # That will remove the utility of making this return a Deferred, since
    # there won't be any work to parallelize.
    if text is not None:
        print(u"Sending text message (%d bytes)" % len(text),
              file=stdout)
        offer = { "message": text }
        fd_to_send = None
        return (offer, fd_to_send)

    what = os.path.join(args.cwd, args.what)
    what = what.rstrip(os.sep)
    if not os.path.exists(what):
        raise TransferError("Cannot send: no file/directory named '%s'" %
                            args.what)
    basename = os.path.basename(what)

    if os.path.isfile(what):
        # we're sending a file
        filesize = os.stat(what).st_size
        offer["file"] = {
            "filename": basename,
            "filesize": filesize,
            }
        print(u"Sending %d byte file named '%s'" % (filesize, basename),
              file=stdout)
        fd_to_send = open(what, "rb")
        return (offer, fd_to_send)

    if os.path.isdir(what):
        print(u"Building zipfile..", file=stdout)
        # We're sending a directory. Create a zipfile in a tempdir and
        # send that.
        fd_to_send = tempfile.SpooledTemporaryFile()
        num_files = 0
        num_bytes = 0
        tostrip = len(what.split(os.sep))
        # TODO: run the blocking zip-the-directory IO in a thread, let
        # the wormhole exchange happen in parallel
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
        offer["directory"] = {
            "mode": "zipfile/deflated",
            "dirname": basename,
            "zipsize": filesize,
            "numbytes": num_bytes,
            "numfiles": num_files,
            }
        print(u"Sending directory (%d bytes compressed) named '%s'"
              % (filesize, basename), file=stdout)
        return (offer, fd_to_send)

    raise TypeError("'%s' is neither file nor directory" % args.what)
