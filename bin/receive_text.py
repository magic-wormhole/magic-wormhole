from __future__ import print_function
import time, json
from wormhole.transcribe import Receiver, list_channels
from wormhole.codes import input_code_with_completion

APPID = "lothar.com/wormhole/text-xfer"

# we're receiving
channel_ids = list_channels()
code = input_code_with_completion("Enter receive-text wormhole code: ",
                                  channel_ids)
start = time.time()
data = json.dumps({"message": "ok"}).encode("utf-8")
r = Receiver(APPID, data, code)
them_bytes = r.get_data()
them_d = json.loads(them_bytes.decode("utf-8"))
print(them_d["message"])
print("elapsed time: %.2f" % (time.time() - start))
