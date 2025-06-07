import unicodedata

from unittest import mock

from .. import util

def test_to_bytes():
    b = util.to_bytes("abc")
    assert isinstance(b, bytes)
    assert b == b"abc"

    A = unicodedata.lookup("LATIN SMALL LETTER A WITH DIAERESIS")
    b = util.to_bytes(A + "bc")
    assert isinstance(b, bytes)
    assert b == b"\xc3\xa4\x62\x63"

def test_bytes_to_hexstr():
    b = b"\x00\x45\x91\xfe\xff"
    hexstr = util.bytes_to_hexstr(b)
    assert isinstance(hexstr, str)
    assert hexstr == "004591feff"

def test_hexstr_to_bytes():
    hexstr = "004591feff"
    b = util.hexstr_to_bytes(hexstr)
    hexstr = util.bytes_to_hexstr(b)
    assert isinstance(b, bytes)
    assert b == b"\x00\x45\x91\xfe\xff"

def test_dict_to_bytes():
    d = {"a": "b"}
    b = util.dict_to_bytes(d)
    assert isinstance(b, bytes)
    assert b == b'{"a": "b"}'

def test_bytes_to_dict():
    b = b'{"a": "b", "c": 2}'
    d = util.bytes_to_dict(b)
    assert isinstance(d, dict)
    assert d == {"a": "b", "c": 2}


def test_free_space():
    free = util.estimate_free_space(".")
    assert isinstance(free, (int, type(None))), repr(free)
    # some platforms (I think the VMs used by travis are in this
    # category) return 0, and windows will return None, so don't assert
    # anything more specific about the return value

def test_no_statvfs():
    # this mock.patch fails on windows, which is sad because windows is
    # the one platform that the code under test was supposed to help with
    try:
        with mock.patch("os.statvfs", side_effect=AttributeError()):
            assert util.estimate_free_space(".") is None
    except AttributeError:  # raised by mock.get_original()
        pass
