from __future__ import print_function
import io, os, sys, json, binascii, six, tempfile, zipfile
from ..blocking.transcribe import Wormhole, WrongPasswordError
from ..blocking.transit import TransitReceiver, TransitError
from ..errors import handle_server_error, TransferError
from .progress import ProgressPrinter

APPID = u"lothar.com/wormhole/text-or-file-xfer"

def receive_blocking(args):
    return BlockingReceiver(args).go()

class RespondError(Exception):
    def __init__(self, response):
        self.response = response

class BlockingReceiver:
    def __init__(self, args):
        assert isinstance(args.relay_url, type(u""))
        self.args = args

    def msg(self, *args, **kwargs):
        print(*args, file=self.args.stdout, **kwargs)

    @handle_server_error
    def go(self):
        with Wormhole(APPID, self.args.relay_url) as w:
            self.handle_code(w)
            verifier = w.get_verifier()
            self.show_verifier(verifier)
            them_d = self.get_data(w)
            try:
                if "message" in them_d:
                    self.handle_text(them_d, w)
                    return 0
                if "file" in them_d:
                    f = self.handle_file(them_d)
                    rp = self.establish_transit(w, them_d)
                    self.transfer_data(rp, f)
                    self.write_file(f)
                    self.close_transit(rp)
                elif "directory" in them_d:
                    f = self.handle_directory(them_d)
                    rp = self.establish_transit(w, them_d)
                    self.transfer_data(rp, f)
                    self.write_directory(f)
                    self.close_transit(rp)
                else:
                    self.msg(u"I don't know what they're offering\n")
                    self.msg(u"Offer details:", them_d)
                    raise RespondError({"error": "unknown offer type"})
            except RespondError as r:
                data = json.dumps(r.response).encode("utf-8")
                w.send_data(data)
                return 1
            return 0

    def handle_code(self, w):
        code = self.args.code
        if self.args.zeromode:
            assert not code
            code = u"0-"
        if not code:
            code = w.input_code("Enter receive wormhole code: ",
                                self.args.code_length)
        w.set_code(code)

    def show_verifier(self, verifier):
        verifier_hex = binascii.hexlify(verifier).decode("ascii")
        if self.args.verify:
            self.msg(u"Verifier %s." % verifier_hex)

    def get_data(self, w):
        try:
            them_bytes = w.get_data()
        except WrongPasswordError as e:
            raise TransferError(u"ERROR: " + e.explain())
        them_d = json.loads(them_bytes.decode("utf-8"))
        if "error" in them_d:
            raise TransferError(u"ERROR: " + them_d["error"])
        return them_d

    def handle_text(self, them_d, w):
        # we're receiving a text message
        self.msg(them_d["message"])
        data = json.dumps({"message_ack": "ok"}).encode("utf-8")
        w.send_data(data)

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
            raise RespondError({"error": "unknown mode"})
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
            raise RespondError({"error": "%s already exists" % mode})
        return abs_destname

    def ask_permission(self):
        while True and not self.args.accept_file:
            ok = six.moves.input("ok? (y/n): ")
            if ok.lower().startswith("y"):
                break
            print(u"transfer rejected", file=sys.stderr)
            raise RespondError({"error": "transfer rejected"})

    def establish_transit(self, w, them_d):
        transit_key = w.derive_key(APPID+u"/transit-key")
        transit_receiver = TransitReceiver(self.args.transit_helper)
        transit_receiver.set_transit_key(transit_key)
        data = json.dumps({
            "file_ack": "ok",
            "transit": {
                "direct_connection_hints": transit_receiver.get_direct_hints(),
                "relay_connection_hints": transit_receiver.get_relay_hints(),
                },
            }).encode("utf-8")
        w.send_data(data)

        # now receive the rest of the owl
        tdata = them_d["transit"]
        transit_receiver.add_their_direct_hints(tdata["direct_connection_hints"])
        transit_receiver.add_their_relay_hints(tdata["relay_connection_hints"])
        record_pipe = transit_receiver.connect()
        return record_pipe

    def transfer_data(self, record_pipe, f):
        self.msg(u"Receiving (%s).." % record_pipe.describe())

        progress_stdout = self.args.stdout
        if self.args.hide_progress:
            progress_stdout = io.StringIO()
        received = 0
        p = ProgressPrinter(self.xfersize, progress_stdout)
        p.start()
        while received < self.xfersize:
            try:
                plaintext = record_pipe.receive_record()
            except TransitError:
                self.msg()
                self.msg(u"Connection dropped before full file received")
                self.msg(u"got %d bytes, wanted %d" % (received, self.xfersize))
                return 1
            f.write(plaintext)
            received += len(plaintext)
            p.update(received)
        p.finish()
        assert received == self.xfersize

    def write_file(self, f):
        tmp_name = f.name
        f.close()
        os.rename(tmp_name, self.abs_destname)
        self.msg(u"Received file written to %s" %
                 os.path.basename(self.abs_destname))

    def write_directory(self, f):
        self.msg(u"Unpacking zipfile..")
        with zipfile.ZipFile(f, "r", zipfile.ZIP_DEFLATED) as zf:
            zf.extractall(path=self.abs_destname)
            # extractall() appears to offer some protection against
            # malicious pathnames. For example, "/tmp/oops" and
            # "../tmp/oops" both do the same thing as the (safe)
            # "tmp/oops".
        self.msg(u"Received files written to %s/" %
                 os.path.basename(self.abs_destname))
        f.close()

    def close_transit(self, record_pipe):
        record_pipe.send_record(b"ok\n")
        record_pipe.close()
