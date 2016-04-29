from __future__ import print_function
import os, sys, json, binascii, six, tempfile, zipfile
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from ..twisted.transcribe import Wormhole
from ..twisted.transit import TransitReceiver
from ..errors import TransferError

APPID = u"lothar.com/wormhole/text-or-file-xfer"

class RespondError(Exception):
    def __init__(self, response):
        self.response = response

def receive(args, reactor=reactor):
    """I implement 'wormhole receive'. I return a Deferred that fires with
    None (for success), or signals one of the following errors:
    * WrongPasswordError: the two sides didn't use matching passwords
    * Timeout: something didn't happen fast enough for our tastes
    * TransferError: the sender rejected the transfer: verifier mismatch
    * any other error: something unexpected happened
    """
    return TwistedReceiver(args, reactor).go()


class TwistedReceiver:
    def __init__(self, args, reactor=reactor):
        assert isinstance(args.relay_url, type(u""))
        self.args = args
        self._reactor = reactor

    def msg(self, *args, **kwargs):
        print(*args, file=self.args.stdout, **kwargs)

    @inlineCallbacks
    def go(self):
        tor_manager = None
        if self.args.tor:
            with self.args.timing.add("import", which="tor_manager"):
                from ..twisted.tor_manager import TorManager
            tor_manager = TorManager(self._reactor, timing=self.args.timing)
            # For now, block everything until Tor has started. Soon: launch
            # tor in parallel with everything else, make sure the TorManager
            # can lazy-provide an endpoint, and overlap the startup process
            # with the user handing off the wormhole code
            yield tor_manager.start()
        w = Wormhole(APPID, self.args.relay_url, tor_manager,
                     timing=self.args.timing,
                     reactor=self._reactor)
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
        d = self._go(w, tor_manager)
        d.addBoth(w.close)
        yield d

    @inlineCallbacks
    def _go(self, w, tor_manager):
        yield self.handle_code(w)
        verifier = yield w.get_verifier()
        self.show_verifier(verifier)
        them_d = yield self.get_data(w)
        try:
            if "message" in them_d:
                yield self.handle_text(them_d, w)
                returnValue(None)
            if "file" in them_d:
                f = self.handle_file(them_d)
                rp = yield self.establish_transit(w, them_d, tor_manager)
                yield self.transfer_data(rp, f)
                self.write_file(f)
                yield self.close_transit(rp)
            elif "directory" in them_d:
                f = self.handle_directory(them_d)
                rp = yield self.establish_transit(w, them_d, tor_manager)
                yield self.transfer_data(rp, f)
                self.write_directory(f)
                yield self.close_transit(rp)
            else:
                self.msg(u"I don't know what they're offering\n")
                self.msg(u"Offer details:", them_d)
                raise RespondError("unknown offer type")
        except RespondError as r:
            data = json.dumps({"error": r.response}).encode("utf-8")
            yield w.send_data(data)
            raise TransferError(r.response)
        returnValue(None)

    @inlineCallbacks
    def handle_code(self, w):
        code = self.args.code
        if self.args.zeromode:
            assert not code
            code = u"0-"
        if not code:
            code = yield w.input_code("Enter receive wormhole code: ",
                                      self.args.code_length)
        yield w.set_code(code)

    def show_verifier(self, verifier):
        verifier_hex = binascii.hexlify(verifier).decode("ascii")
        if self.args.verify:
            self.msg(u"Verifier %s." % verifier_hex)

    @inlineCallbacks
    def get_data(self, w):
        # this may raise WrongPasswordError
        them_bytes = yield w.get_data()
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            raise TransferError(them_d["error"])
        returnValue(them_d)

    @inlineCallbacks
    def handle_text(self, them_d, w):
        # we're receiving a text message
        self.msg(them_d["message"])
        data = json.dumps({"message_ack": "ok"}).encode("utf-8")
        yield w.send_data(data, wait=True)

    def handle_file(self, them_d):
        file_data = them_d["file"]
        self.abs_destname = self.decide_destname("file",
                                                 file_data["filename"])
        self.xfersize = file_data["filesize"]

        self.msg(u"Receiving file (%d bytes) into: %s" %
                 (self.xfersize, os.path.basename(self.abs_destname)))
        self.ask_permission()
        tmp_destname = self.abs_destname + ".tmp"
        return open(tmp_destname, "wb")

    def handle_directory(self, them_d):
        file_data = them_d["directory"]
        zipmode = file_data["mode"]
        if zipmode != "zipfile/deflated":
            self.msg(u"Error: unknown directory-transfer mode '%s'" % (zipmode,))
            raise RespondError("unknown mode")
        self.abs_destname = self.decide_destname("directory",
                                                 file_data["dirname"])
        self.xfersize = file_data["zipsize"]

        self.msg(u"Receiving directory (%d bytes) into: %s/" %
                 (self.xfersize, os.path.basename(self.abs_destname)))
        self.msg(u"%d files, %d bytes (uncompressed)" %
                 (file_data["numfiles"], file_data["numbytes"]))
        self.ask_permission()
        return tempfile.SpooledTemporaryFile()

    def decide_destname(self, mode, destname):
        # the basename() is intended to protect us against
        # "~/.ssh/authorized_keys" and other attacks
        destname = os.path.basename(destname)
        if self.args.output_file:
            destname = self.args.output_file # override
        abs_destname = os.path.join(self.args.cwd, destname)

        # get confirmation from the user before writing to the local directory
        if os.path.exists(abs_destname):
            self.msg(u"Error: refusing to overwrite existing %s %s" %
                     (mode, destname))
            raise RespondError("%s already exists" % mode)
        return abs_destname

    def ask_permission(self):
        with self.args.timing.add("permission", waiting="user") as t:
            while True and not self.args.accept_file:
                ok = six.moves.input("ok? (y/n): ")
                if ok.lower().startswith("y"):
                    break
                print(u"transfer rejected", file=sys.stderr)
                t.detail(answer="no")
                raise RespondError("transfer rejected")
            t.detail(answer="yes")

    @inlineCallbacks
    def establish_transit(self, w, them_d, tor_manager):
        transit_key = w.derive_key(APPID+u"/transit-key")
        transit_receiver = TransitReceiver(self.args.transit_helper,
                                           no_listen=self.args.no_listen,
                                           tor_manager=tor_manager,
                                           reactor=self._reactor,
                                           timing=self.args.timing)
        transit_receiver.set_transit_key(transit_key)
        direct_hints = yield transit_receiver.get_direct_hints()
        relay_hints = yield transit_receiver.get_relay_hints()
        data = json.dumps({
            "file_ack": "ok",
            "transit": {
                "direct_connection_hints": direct_hints,
                "relay_connection_hints": relay_hints,
                },
            }).encode("utf-8")
        yield w.send_data(data)

        # now receive the rest of the owl
        tdata = them_d["transit"]
        transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
        transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
        record_pipe = yield transit_receiver.connect()
        self.args.timing.add("transit connected")
        returnValue(record_pipe)

    @inlineCallbacks
    def transfer_data(self, record_pipe, f):
        self.msg(u"Receiving (%s).." % record_pipe.describe())

        with self.args.timing.add("rx file"):
            progress = tqdm(file=self.args.stdout,
                            disable=self.args.hide_progress,
                            unit="B", unit_scale=True, total=self.xfersize)
            with progress:
                received = yield record_pipe.writeToFile(f, self.xfersize,
                                                         progress.update)

        # except TransitError
        if received < self.xfersize:
            self.msg()
            self.msg(u"Connection dropped before full file received")
            self.msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
            raise TransferError("Connection dropped before full file received")
        assert received == self.xfersize

    def write_file(self, f):
        tmp_name = f.name
        f.close()
        os.rename(tmp_name, self.abs_destname)
        self.msg(u"Received file written to %s" %
                 os.path.basename(self.abs_destname))

    def write_directory(self, f):
        self.msg(u"Unpacking zipfile..")
        with self.args.timing.add("unpack zip"):
            with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
                zf.extractall(path=self.abs_destname)
                # extractall() appears to offer some protection against
                # malicious pathnames. For example, "/tmp/oops" and
                # "../tmp/oops" both do the same thing as the (safe)
                # "tmp/oops".
            self.msg(u"Received files written to %s/" %
                     os.path.basename(self.abs_destname))
            f.close()

    @inlineCallbacks
    def close_transit(self, record_pipe):
        with self.args.timing.add("send ack"):
            yield record_pipe.send_record(b"ok\n")
            yield record_pipe.close()
