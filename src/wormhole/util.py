# No unicode_literals
import json
import os
import unicodedata
from binascii import hexlify, unhexlify
from cryptography.hazmat.primitives.kdf import hkdf
from cryptography.hazmat.primitives import hashes
from attr import attrs, attrib


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
    if isinstance(any, str):
        return any
    return any.decode("ascii")


def bytes_to_hexstr(b):
    assert isinstance(b, bytes)
    hexstr = hexlify(b).decode("ascii")
    assert isinstance(hexstr, str)
    return hexstr


def hexstr_to_bytes(hexstr):
    assert isinstance(hexstr, str)
    b = unhexlify(hexstr.encode("ascii"))
    assert isinstance(b, bytes)
    return b


def dict_to_bytes(d):
    assert isinstance(d, dict)
    b = json.dumps(d).encode("utf-8")
    assert isinstance(b, bytes)
    return b


def bytes_to_dict(b):
    assert isinstance(b, bytes)
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


@attrs(repr=False, slots=True, hash=True)
class _ProvidesValidator:
    interface = attrib()

    def __call__(self, inst, attr, value):
        """
        We use a callable class to be able to change the ``__repr__``.
        """
        if not self.interface.providedBy(value):
            msg = "'{name}' must provide {interface!r} which {value!r} doesn't.".format(
                name=attr.name, interface=self.interface, value=value
            )
            raise TypeError(
                msg,
                attr,
                self.interface,
                value,
            )

    def __repr__(self):
        return f"<provides validator for interface {self.interface!r}>"


def provides(interface):
    """
    A validator that raises a `TypeError` if the initializer is called
    with an object that does not provide the requested *interface* (checks are
    performed using ``interface.providedBy(value)``.

    :param interface: The interface to check for.
    :type interface: ``zope.interface.Interface``

    :raises TypeError: With a human readable error message, the attribute
        (of type `attrs.Attribute`), the expected interface, and the
        value it got.
    """
    return _ProvidesValidator(interface)
