from __future__ import print_function
import sys, json
from wormhole.blocking.transcribe import Receiver, WrongPasswordError

APPID = "lothar.com/wormhole/text-xfer"

def receive_text(args):
    # we're receiving
    r = Receiver(APPID, args.relay_url)
    code = args.code
    if not code:
        code = r.input_code("Enter receive-text wormhole code: ",
                            args.code_length)
    r.set_code(code)
    data = json.dumps({"message": "ok"}).encode("utf-8")
    try:
        them_bytes = r.get_data(data)
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    print(them_d["message"])
