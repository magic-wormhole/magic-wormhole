from __future__ import print_function
import sys, json
from wormhole.blocking.transcribe import Receiver, WrongPasswordError

APPID = "lothar.com/wormhole/text-xfer"

def receive_text(so):
    # we're receiving
    data = json.dumps({"message": "ok"}).encode("utf-8")
    r = Receiver(APPID, data, so.parent["relay-url"])
    code = so["code"]
    if not code:
        code = r.input_code("Enter receive-text wormhole code: ")
    r.set_code(code)
    try:
        them_bytes = r.get_data()
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    print(them_d["message"])
