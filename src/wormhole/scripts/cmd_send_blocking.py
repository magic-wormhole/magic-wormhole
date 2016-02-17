from __future__ import print_function
import json, binascii, six
from ..errors import TransferError
from .progress import ProgressPrinter

def send_blocking(appid, args, phase1, fd_to_send):
    from ..blocking.transcribe import Wormhole, WrongPasswordError
    from ..blocking.transit import TransitSender

    transit_sender = TransitSender(args.transit_helper)
    transit_data = {
        "direct_connection_hints": transit_sender.get_direct_hints(),
        "relay_connection_hints": transit_sender.get_relay_hints(),
        }
    phase1["transit"] = transit_data

    with Wormhole(appid, args.relay_url) as w:
        if args.code:
            w.set_code(args.code)
            code = args.code
        else:
            code = w.get_code(args.code_length)
        if not args.zeromode:
            print(u"Wormhole code is: %s" % code, file=args.stdout)
        print(u"", file=args.stdout)

        if args.verify:
            _do_verify(w)

        my_phase1_bytes = json.dumps(phase1).encode("utf-8")
        w.send_data(my_phase1_bytes)
        try:
            them_phase1_bytes = w.get_data()
        except WrongPasswordError as e:
            raise TransferError(e.explain())
    # note: 'w' is still valid, and we use w.derive_key() below, which can't
    # raise an error that needs to be handled in the 'with' block

    them_phase1 = json.loads(them_phase1_bytes.decode("utf-8"))

    if fd_to_send is None:
        if them_phase1["message_ack"] == "ok":
            print(u"text message sent", file=args.stdout)
            return 0
        raise TransferError("error sending text: %r" % (them_phase1,))

    return _send_file_blocking(w, appid, them_phase1, fd_to_send,
                               transit_sender, args.stdout)

def _do_verify(w):
    verifier = binascii.hexlify(w.get_verifier()).decode("ascii")
    while True:
        ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
        if ok.lower() == "yes":
            break
        if ok.lower() == "no":
            reject_data = json.dumps({"error": "verification rejected",
                                      }).encode("utf-8")
            w.send_data(reject_data)
            raise TransferError("verification rejected, abandoning transfer")

def _send_file_blocking(w, appid, them_phase1, fd_to_send, transit_sender,
                        stdout):

    # we're sending a file, if they accept it

    if "error" in them_phase1:
        raise TransferError("remote error, transfer abandoned: %s"
                            % them_phase1["error"])
    if them_phase1.get("file_ack") != "ok":
        raise TransferError("ambiguous response from remote, "
                            "transfer abandoned: %s" % (them_phase1,))

    tdata = them_phase1["transit"]
    transit_key = w.derive_key(appid+"/transit-key")
    transit_sender.set_transit_key(transit_key)
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_sender.connect()

    print(u"Sending (%s).." % transit_sender.describe(), file=stdout)

    CHUNKSIZE = 64*1024
    fd_to_send.seek(0,2)
    filesize = fd_to_send.tell()
    fd_to_send.seek(0,0)
    p = ProgressPrinter(filesize, stdout)
    with fd_to_send as f:
        sent = 0
        p.start()
        while sent < filesize:
            plaintext = f.read(CHUNKSIZE)
            record_pipe.send_record(plaintext)
            sent += len(plaintext)
            p.update(sent)
        p.finish()

    print(u"File sent.. waiting for confirmation", file=stdout)
    ack = record_pipe.receive_record()
    record_pipe.close()
    if ack == b"ok\n":
        print(u"Confirmation received. Transfer complete.", file=stdout)
        return 0
    raise TransferError("Transfer failed (remote says: %r)" % ack)
