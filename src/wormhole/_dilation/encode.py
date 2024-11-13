import struct

assert len(struct.pack("<L", 0)) == 4
assert len(struct.pack("<Q", 0)) == 8


def to_be4(value):
    if not 0 <= value < 2**32:
        raise ValueError
    return struct.pack(">L", value)


def from_be4(b):
    if not isinstance(b, bytes):
        raise TypeError(repr(b))
    if len(b) != 4:
        raise ValueError
    return struct.unpack(">L", b)[0]
