from __future__ import print_function
import sys, json
from wormhole.blocking.transcribe import Initiator, WrongPasswordError

APPID = "lothar.com/wormhole/text-xfer"

def send_text(args):
    # we're sending
    message = args.text
    data = json.dumps({"message": message,
                       }).encode("utf-8")
    i = Initiator(APPID, data, args.relay_url)
    code = i.get_code(args.code_length)
    print("On the other computer, please run: wormhole receive-text")
    print("Wormhole code is: %s" % code)
    print("")
    try:
        them_bytes = i.get_data()
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    if them_d["message"] == "ok":
        print("text sent")
    else:
        print("error sending text: %r" % (them_d,))

