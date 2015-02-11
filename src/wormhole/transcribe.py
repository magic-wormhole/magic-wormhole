import time, requests, json
from binascii import hexlify, unhexlify
from spake2 import SPAKE2_A, SPAKE2_B
from .const import RELAY
from .codes import make_code, extract_channel_id

SECOND = 1
MINUTE = 60*SECOND

class Timeout(Exception):
    pass

# POST /allocate                                  -> {channel-id: INT}
# POST /CHANNEL-ID/SIDE/pake/post  {message: STR} -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/pake/poll                 -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/data/post  {message: STR} -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/data/poll                 -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/deallocate                -> waiting | deleted

class Common:
    def url(self, suffix):
        return "%s%d/%s/%s" % (self.relay, self.channel_id, self.side, suffix)

    def poll(self, msgs, url_suffix):
        while not msgs:
            if time.time() > (self.started + self.timeout):
                raise Timeout
            time.sleep(self.wait)
            r = requests.post(self.url(url_suffix))
            r.raise_for_status()
            msgs = r.json()["messages"]
        return msgs

class Initiator(Common):
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
        r = requests.post(self.relay + "allocate")
        r.raise_for_status()
        self.channel_id = r.json()["channel-id"]
        self.code = make_code(self.channel_id)
        self.sp = SPAKE2_A(self.code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")
        msg = self.sp.start()
        post_data = {"message": hexlify(msg).decode("ascii")}
        r = requests.post(self.url("pake/post"), data=json.dumps(post_data))
        r.raise_for_status()
        return self.code

    def get_data(self):
        # poll for PAKE response
        msgs = self.poll([], "pake/poll")
        pake_msg = unhexlify(msgs[0].encode("ascii"))
        self.key = self.sp.finish(pake_msg)

        # post encrypted data
        post_data = json.dumps({"message": hexlify(self.data).decode("ascii")})
        r = requests.post(self.url("data/post"), data=post_data)
        r.raise_for_status()
        other_msgs = r.json()["messages"]

        # poll for data message
        msgs = self.poll(other_msgs, "data/poll")
        data = unhexlify(msgs[0].encode("ascii"))

        # deallocate channel
        r = requests.post(self.url("deallocate"))
        r.raise_for_status()

        return data

class Receiver(Common):
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
        post_data = {"message": hexlify(msg).decode("ascii")}
        r = requests.post(self.url("pake/post"), data=json.dumps(post_data))
        r.raise_for_status()
        other_msgs = r.json()["messages"]

        # poll for PAKE response
        msgs = self.poll(other_msgs, "pake/poll")
        pake_msg = unhexlify(msgs[0].encode("ascii"))
        self.key = self.sp.finish(pake_msg)

        # post data message
        post_data = json.dumps({"message": hexlify(self.data).decode("ascii")})
        r = requests.post(self.url("data/post"), data=post_data)
        r.raise_for_status()
        other_msgs = r.json()["messages"]

        # poll for data message
        msgs = self.poll(other_msgs, "data/poll")
        data = unhexlify(msgs[0].encode("ascii"))

        # deallocate channel
        r = requests.post(self.url("deallocate"))
        r.raise_for_status()

        return data
