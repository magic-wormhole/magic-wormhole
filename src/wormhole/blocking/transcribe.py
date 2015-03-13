from __future__ import print_function
import time, requests, json, textwrap
from binascii import hexlify, unhexlify
from spake2 import SPAKE2_A, SPAKE2_B
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from nacl import utils
from .. import codes
from ..util.hkdf import HKDF
from ..const import RENDEZVOUS_RELAY

SECOND = 1
MINUTE = 60*SECOND

class Timeout(Exception):
    pass

class WrongPasswordError(Exception):
    """
    Key confirmation failed.
    """
    # or the data blob was corrupted, and that's why decrypt failed
    def explain(self):
        return textwrap.dedent(self.__doc__)

class InitiatorWrongPasswordError(WrongPasswordError):
    """
    Key confirmation failed. Either your correspondent typed the code wrong,
    or a would-be man-in-the-middle attacker guessed incorrectly. You could
    try again, giving both your correspondent and the attacker another
    chance.
    """

class ReceiverWrongPasswordError(WrongPasswordError):
    """
    Key confirmation failed. Either you typed the code wrong, or a would-be
    man-in-the-middle attacker guessed incorrectly. You could try again,
    giving both you and the attacker another chance.
    """

# POST /allocate                                  -> {channel-id: INT}
#  these return all messages for CHANNEL-ID= and WHICH= but SIDE!=
#  WHICH=(pake,data)
# POST /CHANNEL-ID/SIDE/WHICH/post  {message: STR} -> {messages: [STR..]}
# POST /CHANNEL-ID/SIDE/WHICH/poll                 -> {messages: [STR..]}
# GET  /CHANNEL-ID/SIDE/WHICH/poll (eventsource)   -> STR, STR, ..
#
# POST /CHANNEL-ID/SIDE/deallocate                -> waiting | deleted

class EventSourceFollower:
    def __init__(self, url, timeout):
        self.resp = requests.get(url,
                                 headers={"accept": "text/event-stream"},
                                 stream=True,
                                 timeout=timeout)
        self.resp.raise_for_status()

    def close(self):
        self.resp.close()

    def _get_fields(self, lines):
        while True:
            first_line = lines.next() # raises StopIteration when closed
            fieldname, data = first_line.split(": ", 1)
            data_lines = [data]
            while True:
                next_line = lines.next()
                if not next_line: # empty string, original was "\n"
                    yield (fieldname, "\n".join(data_lines))
                    break
                data_lines.append(next_line)

    def iter_events(self):
        # I think Request.iter_lines and .iter_content use chunk_size= in a
        # funny way, and nothing happens until at least that much data has
        # arrived. So unless we set chunk_size=1, we won't hear about lines
        # for a long time. I'd prefer that chunk_size behaved like
        # read(size), and gave you 1<=x<=size bytes in response.
        lines_iter = self.resp.iter_lines(chunk_size=1)
        for (fieldname, data) in self._get_fields(lines_iter):
            if fieldname == "data":
                yield data
            else:
                print("weird fieldname", fieldname, data)

    def get_message(self):
        return self.iter_events().next()

class Common:
    def url(self, suffix):
        return "%s%d/%s/%s" % (self.relay, self.channel_id, self.side, suffix)

    def get(self, old_msgs, url_suffix):
        # For now, server errors cause the client to fail. TODO: don't. This
        # will require changing the client to re-post messages when the
        # server comes back up.

        # note: while this passes around msgs (plural), our callers really
        # only care about the first one. we use "WHICH" and "SIDE" so that we
        # only expect to see a single message (not our own, where "SIDE" is
        # our own, and not messages for earlier stages, where "WHICH" is
        # different)
        msgs = old_msgs
        while not msgs:
            remaining = self.started + self.timeout - time.time()
            if remaining < 0:
                raise Timeout
            #time.sleep(self.wait)
            f = EventSourceFollower(self.url(url_suffix), remaining)
            msgs = [json.loads(f.get_message())["message"]]
            f.close()
        return msgs

    def _allocate(self):
        r = requests.post(self.relay + "allocate")
        r.raise_for_status()
        channel_id = r.json()["channel-id"]
        return channel_id

    def _post_pake(self):
        msg = self.sp.start()
        post_data = {"message": hexlify(msg).decode("ascii")}
        r = requests.post(self.url("pake/post"), data=json.dumps(post_data))
        r.raise_for_status()
        other_msgs = r.json()["messages"]
        return other_msgs

    def _get_pake(self, other_msgs):
        msgs = self.get(other_msgs, "pake/poll")
        pake_msg = unhexlify(msgs[0].encode("ascii"))
        key = self.sp.finish(pake_msg)
        return key

    def _encrypt_data(self, key, data):
        assert len(key) == SecretBox.KEY_SIZE
        box = SecretBox(key)
        nonce = utils.random(SecretBox.NONCE_SIZE)
        return box.encrypt(self.data, nonce)

    def _post_data(self, data):
        post_data = json.dumps({"message": hexlify(data).decode("ascii")})
        r = requests.post(self.url("data/post"), data=post_data)
        r.raise_for_status()
        other_msgs = r.json()["messages"]
        return other_msgs

    def _get_data(self, other_msgs):
        msgs = self.get(other_msgs, "data/poll")
        data = unhexlify(msgs[0].encode("ascii"))
        return data

    def _decrypt_data(self, key, encrypted):
        assert len(key) == SecretBox.KEY_SIZE
        box = SecretBox(key)
        data = box.decrypt(encrypted)
        return data

    def _deallocate(self):
        r = requests.post(self.url("deallocate"))
        r.raise_for_status()

    def derive_key(self, purpose, length=SecretBox.KEY_SIZE):
        assert type(purpose) == type(b"")
        return HKDF(self.key, length, CTXinfo=purpose)

class Initiator(Common):
    def __init__(self, appid, data, relay=RENDEZVOUS_RELAY):
        self.appid = appid
        self.data = data
        assert relay.endswith("/")
        self.relay = relay
        self.started = time.time()
        self.wait = 0.5*SECOND
        self.timeout = 3*MINUTE
        self.side = "initiator"

    def get_code(self):
        self.channel_id = self._allocate() # allocate channel
        self.code = codes.make_code(self.channel_id)
        self.sp = SPAKE2_A(self.code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")
        self._post_pake()
        return self.code

    def get_data(self):
        key = self._get_pake([])
        self.key = key
        try:
            outbound_key = self.derive_key(b"sender")
            outbound_encrypted = self._encrypt_data(outbound_key, self.data)
            other_msgs = self._post_data(outbound_encrypted)

            inbound_encrypted = self._get_data(other_msgs)
            inbound_key = self.derive_key(b"receiver")
            try:
                inbound_data = self._decrypt_data(inbound_key,
                                                  inbound_encrypted)
            except CryptoError:
                raise InitiatorWrongPasswordError
        finally:
            self._deallocate()
        return inbound_data


class Receiver(Common):
    def __init__(self, appid, data, relay=RENDEZVOUS_RELAY):
        self.appid = appid
        self.data = data
        self.relay = relay
        assert relay.endswith("/")
        self.started = time.time()
        self.wait = 0.5*SECOND
        self.timeout = 3*MINUTE
        self.side = "receiver"
        self.code = None
        self.channel_id = None

    def list_channels(self):
        r = requests.get(self.relay + "list")
        r.raise_for_status()
        channel_ids = r.json()["channel-ids"]
        return channel_ids

    def input_code(self, prompt="Enter wormhole code: "):
        code = codes.input_code_with_completion(prompt, self.list_channels)
        return code

    def set_code(self, code):
        assert self.code is None
        assert self.channel_id is None
        self.code = code
        self.channel_id = codes.extract_channel_id(code)
        self.sp = SPAKE2_B(code.encode("ascii"),
                           idA=self.appid+":Initiator",
                           idB=self.appid+":Receiver")

    def get_data(self):
        assert self.code is not None
        assert self.channel_id is not None
        other_msgs = self._post_pake()
        key = self._get_pake(other_msgs)
        self.key = key

        try:
            outbound_key = self.derive_key(b"receiver")
            outbound_encrypted = self._encrypt_data(outbound_key, self.data)
            other_msgs = self._post_data(outbound_encrypted)

            inbound_encrypted = self._get_data(other_msgs)
            inbound_key = self.derive_key(b"sender")
            try:
                inbound_data = self._decrypt_data(inbound_key,
                                                  inbound_encrypted)
            except CryptoError:
                raise ReceiverWrongPasswordError
        finally:
            self._deallocate()
        return inbound_data
