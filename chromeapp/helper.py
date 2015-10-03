#!/usr/local/bin/python
import os, sys, json, struct

while True:
    with open("/tmp/out.txt","a") as f:
        f.write("called\n")
    l = struct.unpack("@I", sys.stdin.read(4))[0]
    data = json.loads(sys.stdin.read(l).decode("utf-8"))
    with open("/tmp/out.txt","a") as f:
        f.write("message:\n")
        f.write(json.dumps(data).encode("utf-8")+"\n")
    response = {"ok": "yeah"}
    response_bytes = json.dumps(response).encode("utf-8")
    sys.stdout.write(struct.pack("@I", len(response_bytes)))
    sys.stdout.write(response_bytes)
    sys.stdout.flush()

sys.exit(0)
