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


def sizeof_fmt(num, suffix='B', units=None, power=None, sep=' ', precision=2):
    for unit in units[:-1]:
        if abs(round(num, precision)) < power:
            if isinstance(num, int):
                return "{}{}{}{}".format(num, sep, unit, suffix)
            else:
                return "{:3.{}f}{}{}{}".format(num, precision, sep, unit, suffix)
        num /= float(power)
    return "{:.{}f}{}{}{}".format(num, precision, sep, units[-1], suffix)


def sizeof_fmt_iec(num, suffix='B', sep=' ', precision=2):
    return sizeof_fmt(num, suffix=suffix, sep=sep, precision=precision, units=['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi', 'Yi'], power=1024)


def sizeof_fmt_decimal(num, suffix='B', sep=' ', precision=2):
    return sizeof_fmt(num, suffix=suffix, sep=sep, precision=precision, units=['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y'], power=1000)
