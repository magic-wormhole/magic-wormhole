from __future__ import print_function
import json, binascii, six
from ..errors import TransferError
from .progress import ProgressPrinter
from ..blocking.transcribe import Wormhole, WrongPasswordError
from ..blocking.transit import TransitSender
from ..errors import handle_server_error
from .send_common import (APPID, handle_zero, build_other_command,
                          build_phase1_data)

@handle_server_error
def send_blocking(args):
    assert isinstance(args.relay_url, type(u""))
    handle_zero(args)
    phase1, fd_to_send = build_phase1_data(args)
    other_cmd = build_other_command(args)
    print(u"On the other computer, please run: %s" % other_cmd,
          file=args.stdout)

    if fd_to_send is not None:
        transit_sender = TransitSender(args.transit_helper)
        transit_data = {
            "direct_connection_hints": transit_sender.get_direct_hints(),
            "relay_connection_hints": transit_sender.get_relay_hints(),
            }
        phase1["transit"] = transit_data

    with Wormhole(APPID, args.relay_url) as w:
        if args.code:
            w.set_code(args.code)
            code = args.code
        else:
            code = w.get_code(args.code_length)
        if not args.zeromode:
            print(u"Wormhole code is: %s" % code, file=args.stdout)
        print(u"", file=args.stdout)

        # get the verifier, because that also lets us derive the transit key,
        # which we want to set before revealing the connection hints to the
        # far side, so we'll be ready for them when they connect
        verifier = binascii.hexlify(w.get_verifier()).decode("ascii")
        if args.verify:
            _do_verify(verifier, w)

        if fd_to_send is not None:
            transit_key = w.derive_key(APPID+"/transit-key")
            transit_sender.set_transit_key(transit_key)

        my_phase1_bytes = json.dumps(phase1).encode("utf-8")
        w.send_data(my_phase1_bytes)
        try:
            them_phase1_bytes = w.get_data()
        except WrongPasswordError as e:
            raise TransferError(e.explain())

    them_phase1 = json.loads(them_phase1_bytes.decode("utf-8"))

    if fd_to_send is None:
        if them_phase1["message_ack"] == "ok":
            print(u"text message sent", file=args.stdout)
            return 0
        raise TransferError("error sending text: %r" % (them_phase1,))

    return _send_file_blocking(them_phase1, fd_to_send,
                               transit_sender, args.stdout, args.hide_progress)

def _do_verify(verifier, w):
    while True:
        ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
        if ok.lower() == "yes":
            break
        if ok.lower() == "no":
            reject_data = json.dumps({"error": "verification rejected",
                                      }).encode("utf-8")
            w.send_data(reject_data)
            raise TransferError("verification rejected, abandoning transfer")

def _send_file_blocking(them_phase1, fd_to_send, transit_sender,
                        stdout, hide_progress):

    # we're sending a file, if they accept it

    if "error" in them_phase1:
        raise TransferError("remote error, transfer abandoned: %s"
                            % them_phase1["error"])
    if them_phase1.get("file_ack") != "ok":
        raise TransferError("ambiguous response from remote, "
                            "transfer abandoned: %s" % (them_phase1,))

    tdata = them_phase1["transit"]
    transit_sender.add_their_direct_hints(tdata["direct_connection_hints"])
    transit_sender.add_their_relay_hints(tdata["relay_connection_hints"])
    record_pipe = transit_sender.connect()

    print(u"Sending (%s).." % record_pipe.describe(), file=stdout)

    CHUNKSIZE = 64*1024
    fd_to_send.seek(0,2)
    filesize = fd_to_send.tell()
    fd_to_send.seek(0,0)
    p = ProgressPrinter(filesize, stdout)
    with fd_to_send as f:
        sent = 0
        if not hide_progress:
            p.start()
        while sent < filesize:
            plaintext = f.read(CHUNKSIZE)
            record_pipe.send_record(plaintext)
            sent += len(plaintext)
            if not hide_progress:
                p.update(sent)
        if not hide_progress:
            p.finish()

    print(u"File sent.. waiting for confirmation", file=stdout)
    ack = record_pipe.receive_record()
    record_pipe.close()
    if ack == b"ok\n":
        print(u"Confirmation received. Transfer complete.", file=stdout)
        return 0
    raise TransferError("Transfer failed (remote says: %r)" % ack)
