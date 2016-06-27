from __future__ import print_function
import time, json

# Run this as 'watch python misc/dump-stats.py' against a 'wormhole-server
# start --stats-file=stats.json'

with open("stats.json") as f:
    data_s = f.read()

now = time.time()
data = json.loads(data_s)
if now < data["valid_until"]:
    valid = "valid"
else:
    valid = "EXPIRED"
age = now - data["created"]

print("age: %d (%s)" % (age, valid))
print(data_s)
