import time, requests, json
from binascii import hexlify, unhexlify
from spake2 import SPAKE2_A, SPAKE2_B
from .const import RELAY
from .codes import make_code, extract_channel_id

SECOND = 1
MINUTE = 60*SECOND

class Timeout(Exception):
    pass

# POST /allocate                                       -> {channel-id: INT}
# POST /pake/post/CHANNEL-ID {side: STR, message: STR} -> {messages: [STR..]}
# POST /pake/poll/CHANNEL-ID {side: STR}               -> {messages: [STR..]}
# POST /data/post/CHANNEL-ID {side: STR, message: STR} -> {messages: [STR..]}
# POST /data/poll/CHANNEL-ID {side: STR}               -> {messages: [STR..]}
# POST /deallocate/CHANNEL-ID {side: STR}              -> waiting | ok

class Initiator:
    def __init__(self, appid, data, relay=RELAY):
        self.appid = appid
        self.data = data
        assert relay.endswith("/")
        self.relay = relay
        self.started = time.time()
        self.wait = 2*SECOND
        self.timeout = 3*MINUTE
        self.side = "initiator"

    def get_code(self):
        # allocate channel
        r = requests.post(self.relay + "allocate", data="{}")
        r.raise_for_status()
        self.channel_id = r.json()["channel-id"]
        self.code = codes.make_code(self.channel_id)
        self.sp = SPAKE2_A(self.code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")
        msg = self.sp.start()
        post_url = self.relay + "pake/post/%d" % self.channel_id
        post_data = {"side": self.side,
                     "message": hexlify(msg).decode("ascii")}
        r = requests.post(post_url, data=json.dumps(post_data))
        r.raise_for_status()
        return self.code

    def get_data(self):
        # poll for PAKE response
        pake_url = self.relay + "pake/poll/%d" % self.channel_id
        post_data = json.dumps({"side": self.side})
        while True:
            r = requests.post(pake_url, data=post_data)
            r.raise_for_status()
            msgs = r.json()["messages"]
            if msgs:
                break
            if time.time() > (self.started + self.timeout):
                raise Timeout
            time.sleep(self.wait)
        pake_msg = unhexlify(msgs[0].encode("ascii"))
        self.key = self.sp.finish(pake_msg)

        # post encrypted data
        post_url = self.relay + "data/post/%d" % self.channel_id
        post_data = json.dumps({"side": self.side,
                                "message": hexlify(self.data).decode("ascii")})
        r = requests.post(post_url, data=post_data)
        r.raise_for_status()

        # poll for data message
        data_url = self.relay + "data/poll/%d" % self.channel_id
        post_data = json.dumps({"side": self.side})
        while True:
            r = requests.post(data_url, data=post_data)
            r.raise_for_status()
            msgs = r.json()["messages"]
            if msgs:
                break
            if time.time() > (self.started + self.timeout):
                raise Timeout
            time.sleep(self.wait)
        data = unhexlify(msgs[0].encode("ascii"))

        # deallocate channel
        deallocate_url = self.relay + "deallocate/%s" % self.channel_id
        post_data = json.dumps({"side": self.side})
        r = requests.post(deallocate, data=post_data)
        r.raise_for_status()

        return data

class Receiver:
    def __init__(self, appid, data, code, relay=RELAY):
        self.appid = appid
        self.data = data
        self.code = code
        self.channel_id = extract_channel_id(code)
        self.relay = relay
        assert relay.endswith("/")
        self.started = time.time()
        self.wait = 2*SECOND
        self.timeout = 3*MINUTE
        self.side = "receiver"
        self.sp = SPAKE2_B(code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")

    def get_data(self):
        # post PAKE message
        msg = self.sp.start()
        post_url = self.relay + "pake/post/%d" % self.channel_id
        post_data = {"side": self.side,
                     "message": hexlify(msg).decode("ascii")}
        r = requests.post(post_url, data=json.dumps(post_data))
        r.raise_for_status()

        # poll for PAKE response
        pake_url = self.relay + "pake/poll/%d" % self.channel_id
        post_data = json.dumps({"side": self.side})
        while True:
            r = requests.post(pake_url, data=post_data)
            r.raise_for_status()
            msgs = r.json()["messages"]
            if msgs:
                break
            if time.time() > (self.started + self.timeout):
                raise Timeout
            time.sleep(self.wait)
        pake_msg = unhexlify(msgs[0].encode("ascii"))
        self.key = self.sp.finish(pake_msg)

        # post data message
        post_url = self.relay + "data/post/%d" % self.channel_id
        post_data = json.dumps({"side": self.side,
                                "message": hexlify(self.data).decode("ascii")})
        r = requests.post(post_url, data=post_data)
        r.raise_for_status()

        # poll for data message
        data_url = self.relay + "data/poll/%d" % self.channel_id
        post_data = json.dumps({"side": self.side})
        while True:
            r = requests.post(data_url, data=post_data)
            r.raise_for_status()
            msgs = r.json()["messages"]
            if msgs:
                break
            if time.time() > (self.started + self.timeout):
                raise Timeout
            time.sleep(self.wait)
        data = unhexlify(msgs[0].encode("ascii"))

        # deallocate channel
        deallocate_url = self.relay + "deallocate/%s" % self.channel_id
        post_data = json.dumps({"side": self.side})
        r = requests.post(deallocate, data=post_data)
        r.raise_for_status()

        return data
