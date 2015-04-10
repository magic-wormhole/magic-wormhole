from __future__ import print_function
import sys, json, binascii
from ..errors import handle_server_error

APPID = "lothar.com/wormhole/text-xfer"

@handle_server_error
def send_text(args):
    # we're sending
    from ..blocking.transcribe import Initiator, WrongPasswordError

    i = Initiator(APPID, args.relay_url)
    if args.zeromode:
        assert not args.code
        args.code = "0-"
    if args.code:
        i.set_code(args.code)
        code = args.code
    else:
        code = i.get_code(args.code_length)
    other_cmd = "wormhole receive-text"
    if args.verify:
        other_cmd = "wormhole --verify receive-text"
    if args.zeromode:
        other_cmd += " -0"
    print("On the other computer, please run: %s" % other_cmd)
    if not args.zeromode:
        print("Wormhole code is: %s" % code)
    print("")

    if args.verify:
        verifier = binascii.hexlify(i.get_verifier())
        while True:
            ok = raw_input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                print("verification rejected, abandoning transfer",
                      file=sys.stderr)
                reject_data = json.dumps({"error": "verification rejected",
                                          }).encode("utf-8")
                i.get_data(reject_data)
                return 1

    message = args.text
    data = json.dumps({"message": message,
                       }).encode("utf-8")
    try:
        them_bytes = i.get_data(data)
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    if them_d["message"] == "ok":
        print("text sent")
    else:
        print("error sending text: %r" % (them_d,))

