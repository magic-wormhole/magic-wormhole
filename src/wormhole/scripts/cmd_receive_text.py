from __future__ import print_function
import sys, json, binascii
from ..errors import handle_server_error

APPID = "lothar.com/wormhole/text-xfer"

@handle_server_error
def receive_text(args):
    # we're receiving
    from ..blocking.transcribe import Wormhole, WrongPasswordError

    w = Wormhole(APPID, args.relay_url)
    if args.zeromode:
        assert not args.code
        args.code = "0-"
    code = args.code
    if not code:
        code = w.input_code("Enter receive-text wormhole code: ",
                            args.code_length)
    w.set_code(code)

    if args.verify:
        verifier = binascii.hexlify(w.get_verifier())
        print("Verifier %s." % verifier)

    data = json.dumps({"message": "ok"}).encode("utf-8")
    try:
        them_bytes = w.get_data(data)
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    if "error" in them_d:
        print("ERROR: " + them_d["error"], file=sys.stderr)
        return 1
    print(them_d["message"])
