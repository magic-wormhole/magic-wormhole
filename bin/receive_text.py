from __future__ import print_function
import sys, time, json
from wormhole.blocking.transcribe import Receiver, WrongPasswordError

APPID = "lothar.com/wormhole/text-xfer"

# we're receiving
data = json.dumps({"message": "ok"}).encode("utf-8")
r = Receiver(APPID, data)
r.set_code(r.input_code("Enter receive-text wormhole code: "))
start = time.time()
try:
    them_bytes = r.get_data()
except WrongPasswordError as e:
    print("ERROR: " + e.explain(), file=sys.stderr)
    sys.exit(1)
them_d = json.loads(them_bytes.decode("utf-8"))
print(them_d["message"])
print("elapsed time: %.2f" % (time.time() - start))
