from __future__ import print_function
import os, sys, six, tempfile, zipfile, hashlib
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import log
from ..wormhole import wormhole
from ..transit import TransitReceiver
from ..errors import TransferError, WormholeClosedError
from ..util import dict_to_bytes, bytes_to_dict, bytes_to_hexstr
from .verify import verify

APPID = u"lothar.com/wormhole/text-or-file-xfer"

class RespondError(Exception):
    def __init__(self, response):
        self.response = response

def _msg(self, *args, **kwargs):
    print(*args, file=self.args.stdout, **kwargs)

@inlineCallbacks
def receive(args, reactor=reactor):
    """I implement 'wormhole receive'. I return a Deferred that fires with
    None (for success), or signals one of the following errors:
    * WrongPasswordError: the two sides didn't use matching passwords
    * Timeout: something didn't happen fast enough for our tastes
    * TransferError: the sender rejected the transfer: verifier mismatch
    * any other error: something unexpected happened
    """

    w = yield _build_wormhole(args, reactor)
    try:
        retval = yield _do_receive(w, args, reactor)
    finally:
        yield w.close()
    returnValue(retval)
    # I wanted to do this instead:
    #
    #    try:
    #        yield self._go(w, tor_manager)
    #    finally:
    #        yield w.close()
    #
    # but when _go had a UsageError, the stacktrace was always displayed
    # as coming from the "yield self._go" line, which wasn't very useful
    # for tracking it down.
    #d = self._go(w)
    #d.addBoth(w.close)
    #yield d

@inlineCallbacks
def _build_wormhole(args, reactor):
    tor_manager = None
    if args.tor:
        with args.timing.add("import", which="tor_manager"):
            from ..tor_manager import TorManager
        tor_manager = TorManager(reactor, timing=args.timing)
        # For now, block everything until Tor has started. Soon: launch
        # tor in parallel with everything else, make sure the TorManager
        # can lazy-provide an endpoint, and overlap the startup process
        # with the user handing off the wormhole code
        yield tor_manager.start()

    wormhole_args = {}

    code = args.code
    if args.zeromode:
        assert not code
        code = u"0-"
    if code:
        wormhole_args["code"] = code
    else:
        prompt = "Enter receive wormhole code: "
        asker = CodeAsker(stdin, stdout, prompt, length=args.code_length)
        wormhole_args["asker"] = asker

    w = yield wormhole(APPID, args.relay_url, reactor, tor_manager=tor_manager,
                       timing=args.timing, **wormhole_args)
    returnValue(w)

# expected messages:
# -> [{verify:True}] - optional
# -> {offer:{message:text}} or {offer:{file|directory:{stuff}}}
# <- {answer:{message_ack|file_ack:ok}}
# (transit stuff, wormhole.embiggen)


def _do_receive(w, args, reactor):
    them_bytes = yield w.read()
    them_d = bytes_to_dict(them_bytes)

    if args.verify or them_d.get("verify", False):
        verifier_bytes = yield w.verify()
        (res, err) = yield deferToThread(verify, verifier_bytes)
        if not res:
            w.send(dict_to_bytes({"error": err}))
            raise TransferError(err) # causes wormhole() to re-raise
        them_bytes = yield w.read()
        them_d = bytes_to_dict(them_bytes)

    if "error" in them_d:
        raise TransferError(them_d["error"])

    #print("GOT", them_d)
    offer = them_d[u"offer"]
    if "message" in them_d:
        # we're receiving a text message
        self._msg(them_d["message"])
        w.send(dict_to_bytes({"answer": {"message_ack": "ok"}}))
        returnValue(None)

    try:
        if "file" in them_d:
            file_data = them_d["file"]
            abs_destname = _decide_destname(args, "file", file_data["filename"])
            self.xfersize = file_data["filesize"]

            self._msg(u"Receiving file (%d bytes) into: %s" %
                      (self.xfersize, os.path.basename(abs_destname)))
            _ask_permission(args)
            tmp_destname = abs_destname + ".tmp"
            f = open(tmp_destname, "wb")
        elif "directory" in them_d:
            file_data = them_d["directory"]
            zipmode = file_data["mode"]
            if zipmode != "zipfile/deflated":
                self._msg(u"Error: unknown directory-transfer mode '%s'" % (zipmode,))
                raise RespondError("unknown mode")
            abs_destname = _decide_destname(args, "directory", file_data["dirname"])
            self.xfersize = file_data["zipsize"]

            self._msg(u"Receiving directory (%d bytes) into: %s/" %
                      (self.xfersize, os.path.basename(abs_destname)))
            self._msg(u"%d files, %d bytes (uncompressed)" %
                      (file_data["numfiles"], file_data["numbytes"]))
            _ask_permission(args)
            f = tempfile.SpooledTemporaryFile()
        else:
            self._msg(u"I don't know what they're offering\n")
            self._msg(u"Offer details: %r" % (them_d,))
            raise RespondError("unknown offer type")
    except RespondError as r:
        w.send(dict_to_bytes({"error": r.response}))
        raise TransferError(r.response)

    w.send(dict_to_bytes({"answer": { "file_ack": "ok" }}))

    big_w = yield w.embiggen()
    args.timing.add("transit connected")

    # now receive the rest of the owl
    self._msg(u"Receiving (%s).." % big_w.describe())

    with args.timing.add("rx file"):
        progress = tqdm(file=args.stdout,
                        disable=args.hide_progress,
                        unit="B", unit_scale=True, total=self.xfersize)
        hasher = hashlib.sha256()
        with progress:
            received = yield big_w.writeToFile(f, self.xfersize,
                                               progress.update,
                                               hasher.update)
        datahash = hasher.digest()

    # except TransitError
    if received < self.xfersize:
        self._msg()
        self._msg(u"Connection dropped before full file received")
        self._msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
        raise TransferError("Connection dropped before full file received")
    assert received == self.xfersize

    if "file" in them_d:
        tmp_name = f.name
        f.close()
        os.rename(tmp_name, abs_destname)
        self._msg(u"Received file written to %s" %
                  os.path.basename(abs_destname))
    elif "directory" in them_d:
        self._msg(u"Unpacking zipfile..")
        with args.timing.add("unpack zip"):
            with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
                for info in zf.infolist():
                    _extract_file(zf, info, abs_destname)
            self._msg(u"Received files written to %s/" %
                      os.path.basename(abs_destname))
            f.close()

    datahash_hex = bytes_to_hexstr(datahash)
    ack = {u"ack": u"ok", u"sha256": datahash_hex}
    ack_bytes = dict_to_bytes(ack)
    with args.timing.add("send ack"):
        yield big_w.send_record(ack_bytes)
        yield big_w.close()

def _decide_destname(args, mode, destname):
    # the basename() is intended to protect us against
    # "~/.ssh/authorized_keys" and other attacks
    destname = os.path.basename(destname)
    if args.output_file:
        destname = args.output_file # override
    abs_destname = os.path.abspath( os.path.join(args.cwd, destname) )

    # get confirmation from the user before writing to the local directory
    if os.path.exists(abs_destname):
        self._msg(u"Error: refusing to overwrite existing %s %s" %
                  (mode, destname))
        raise RespondError("%s already exists" % mode)
    return abs_destname

def _extract_file(zf, info, extract_dir):
    """
    the zipfile module does not restore file permissions
    so we'll do it manually
    """
    out_path = os.path.join( extract_dir, info.filename )
    out_path = os.path.abspath( out_path )
    if not out_path.startswith( extract_dir ):
        raise ValueError( "malicious zipfile, %s outside of extract_dir %s"
                % (info.filename, extract_dir) )

    zf.extract( info.filename, path=extract_dir )

    # not sure why zipfiles store the perms 16 bits away but they do
    perm = info.external_attr >> 16
    os.chmod( out_path, perm )

def _send_data(data, w):
    data_bytes = dict_to_bytes(data)
    w.send(data_bytes)

def _ask_permission(args):
    with args.timing.add("permission", waiting="user") as t:
        while True and not args.accept_file:
            ok = six.moves.input("ok? (y/n): ")
            if ok.lower().startswith("y"):
                break
            print(u"transfer rejected", file=sys.stderr)
            t.detail(answer="no")
            raise RespondError("transfer rejected")
        t.detail(answer="yes")
