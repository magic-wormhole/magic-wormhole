# No unicode_literals
import json
import os
import unicodedata
from binascii import hexlify, unhexlify
from cryptography.hazmat.primitives.kdf import hkdf
from cryptography.hazmat.primitives import hashes


def HKDF(skm, outlen, salt=None, CTXinfo=b""):
    """
    Return the RFC5869 'HMAC-based Key Derivation Function' result of
    using the given `salt`, tag from `CTXinfo` and secret from `skm`
    with a SHA256 hash.

    :param bytes skm: the input key material
    :param int outlen: output length, in bytes
    :param bytes salt: optional salt value (None for no salt)
    :param bytes CTXinfo: context / application-specific string (default is empty)

    :return bytes: the derived key material
    """
    return hkdf.HKDF(
        hashes.SHA256(),
        outlen,
        salt,
        CTXinfo,
    ).derive(skm)


def to_bytes(u):
    return unicodedata.normalize("NFC", u).encode("utf-8")


def to_unicode(any):
    if isinstance(any, type(u"")):
        return any
    return any.decode("ascii")


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


def estimate_free_space(target):
    # f_bfree is the blocks available to a root user. It might be more
    # accurate to use f_bavail (blocks available to non-root user), but we
    # don't know which user is running us, and a lot of installations don't
    # bother with reserving extra space for root, so let's just stick to the
    # basic (larger) estimate.
    try:
        s = os.statvfs(os.path.dirname(os.path.abspath(target)))
        return s.f_frsize * s.f_bfree
    except AttributeError:
        return None
