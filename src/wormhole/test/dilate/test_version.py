from wormhole._dilation.manager import _find_shared_versions


def test_versions():
    assert _find_shared_versions([],[]) is None
    assert _find_shared_versions(["foo"],["foo"]) == "foo"
    assert _find_shared_versions(["foo","bar"],["foo"]) == "foo"
    assert _find_shared_versions(["bar","foo"],["foo","bar"]) == "bar"
    assert _find_shared_versions([],["foo","bar"]) is None
    assert _find_shared_versions(["foo","bar"],[]) is None
