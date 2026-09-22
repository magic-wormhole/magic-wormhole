import struct
from hashlib import sha256
from ..util import to_bytes
from .ikeysetup import MessageTuple

def encode_bytes(b):
    assert len(b) < 2**32
    return struct.pack(">L", len(b)) + b
def encode_str(s):
    return encode_bytes(to_bytes(s))

def hash_transcript(version: str, transcript: list[MessageTuple]) -> bytes:
    # sort the messages: first by side, then by phase
    messages = transcript.copy()
    # conveniently the same order the tuples are in
    messages.sort()
    # encode each, reversibly
    h = sha256(encode_str(version))
    for (side, phase, body) in messages:
        assert isinstance(side, str)
        assert isinstance(phase, str)
        assert isinstance(body, bytes)
        h.update(encode_str(side))
        h.update(encode_str(phase))
        h.update(encode_bytes(body))
    return h.digest()
