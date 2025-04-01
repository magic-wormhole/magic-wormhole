from ..._dilation.encode import to_be4, from_be4
import pytest


def test_be4():
    assert to_be4(0) == b"\x00\x00\x00\x00"
    assert to_be4(1) == b"\x00\x00\x00\x01"
    assert to_be4(256) == b"\x00\x00\x01\x00"
    assert to_be4(257) == b"\x00\x00\x01\x01"
    with pytest.raises(ValueError):
        to_be4(-1)
    with pytest.raises(ValueError):
        to_be4(2**32)

    assert from_be4(b"\x00\x00\x00\x00") == 0
    assert from_be4(b"\x00\x00\x00\x01") == 1
    assert from_be4(b"\x00\x00\x01\x00") == 256
    assert from_be4(b"\x00\x00\x01\x01") == 257

    with pytest.raises(TypeError):
        from_be4(0)
    with pytest.raises(ValueError):
        from_be4(b"\x01\x00\x00\x00\x00")
