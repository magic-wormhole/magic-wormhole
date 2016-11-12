# No unicode_literals
import json, unicodedata
from binascii import hexlify, unhexlify

def to_bytes(u):
    return unicodedata.normalize("NFC", u).encode("utf-8")
def bytes_to_hexstr(b):
    assert isinstance(b, type(b""))
    hexstr = hexlify(b).decode("ascii")
    assert isinstance(hexstr, type(u""))
    return hexstr
def hexstr_to_bytes(hexstr):
    assert isinstance(hexstr, type(u""))
    b = unhexlify(hexstr.encode("ascii"))
    assert isinstance(b, type(b""))
    return b
def dict_to_bytes(d):
    assert isinstance(d, dict)
    b = json.dumps(d).encode("utf-8")
    assert isinstance(b, type(b""))
    return b
def bytes_to_dict(b):
    assert isinstance(b, type(b""))
    d = json.loads(b.decode("utf-8"))
    assert isinstance(d, dict)
    return d
