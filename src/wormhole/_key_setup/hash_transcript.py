from hashlib import sha256

def encode_bytes(b):
    assert len(b) < 2**32
    return struct.pack(">L", b) + b
def encode_str(s):
    return encode_bytes(to_bytes(s))

def hash_transcript(version: str, transcript: list[(str,str,bytes)]) -> bytes:
    # sort the messages: first by side, then by phase
    messages = transcript.copy()
    # conveniently the same order the tuples are in
    messages.sort()
    # encode each, reversibly
    h = sha256(version)
    for (side, phase, body) in messages:
        h.update(encode_str(side))
        h.update(encode_str(phase))
        h.update(encode_bytes(body))
    return h.digest()
