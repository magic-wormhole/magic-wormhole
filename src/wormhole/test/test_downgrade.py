from unittest import mock
from pytest_twisted import ensureDeferred
import pytest

from .. import wormhole
from ..errors import NoCommonVersionError, WrongPasswordError
from ..util import bytes_to_dict, dict_to_bytes
from .._mailbox import Mailbox
from .._key_setup.negotiator import KEY_SETUP_VERSIONS, KEY_SETUP_CONSTRUCTORS


# Any client which accepts the v0 protocol is vulnerable to a
# downgrade attack, because v0 did not include a hash of the
# transcript (specifically the "my_key_setup_versions"
# advertisement). Demonstrate that v0-capable clients are vulnerable,
# and v0-incapable clients are not.

APPID = "appid"

original_rx_message = Mailbox.rx_message

def corrupt(remove=[], remove_all=False):
    def new_rx_message(self, side, phase, body):
        assert isinstance(side, str)
        assert isinstance(phase, str)
        assert isinstance(body, bytes)
        if phase == "pake":
            data = bytes_to_dict(body)
            if "my_key_setup_versions" in data:
                vers = data["my_key_setup_versions"]
                if remove:
                    vers = [ver for ver in vers if ver not in remove]
                    data["my_key_setup_versions"] = vers
                if remove_all:
                    del data["my_key_setup_versions"]
            body = dict_to_bytes(data)
        return original_rx_message(self, side, phase, body)
    return mock.patch("wormhole._mailbox.Mailbox.rx_message", new_rx_message)


async def make_connection(reactor, mailbox, status):
    def on_status_update1(s):
        status[0] = s
    def on_status_update2(s):
        status[1] = s
    w1 = wormhole.create(APPID, mailbox.url, reactor, on_status_update=on_status_update1)
    w2 = wormhole.create(APPID, mailbox.url, reactor, on_status_update=on_status_update2)

    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)

    versions1 = await w1.get_versions()
    versions2 = await w2.get_versions()
    assert versions1 == {}
    assert versions2 == {}

    return status

@ensureDeferred
async def test_default_version(reactor, mailbox):
    expected = KEY_SETUP_VERSIONS[-1]

    status = [None, None]
    await make_connection(reactor, mailbox, status)
    assert status[0].key_setup_version == expected
    assert status[1].key_setup_version == expected

def do_v0():
    return mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0"])

# v0-only clients: we always negotiate v0, not much to test or show

# these tests only work if we can do v0: if we remove that code,
# remove these tests
assert "v0" in KEY_SETUP_CONSTRUCTORS

@ensureDeferred
async def test_v0_good(reactor, mailbox):

    status = [None, None]
    with do_v0():
        await make_connection(reactor, mailbox, status)
    assert status[0].key_setup_version == "v0"
    assert status[1].key_setup_version == "v0"

# v0+v1 clients: The only possible downgrade attack will remove v1,
# leaving v0 as the only possible option. Either we accept v0 (and are
# vulnerable to the attack), or we don't (NoCommonVersionError)

# these tests only work if we can do both v0 and v1: if we remove
# either, change these tests
assert "v0" in KEY_SETUP_CONSTRUCTORS
assert "v1" in KEY_SETUP_CONSTRUCTORS

def do_v0v1():
    return mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0", "v1"])
def do_v1():
    return mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v1"])

@ensureDeferred
async def test_v0v1_remove_v1_vulnerable(reactor, mailbox):
    status = [None, None]
    with (do_v0v1(), corrupt(remove=["v1"])):
        await make_connection(reactor, mailbox, status)
    assert status[0].key_setup_version == "v0"
    assert status[1].key_setup_version == "v0"

@ensureDeferred
async def test_v1_remove_v1_no_common(reactor, mailbox, observe_errors):
    status = [None, None]
    with (do_v1(), corrupt(remove_all=True)):
        with pytest.raises(NoCommonVersionError) as err:
            await make_connection(reactor, mailbox, status)
        assert err.value.my_versions == ["v1"]
        assert err.value.their_versions == ["v0"] # legacy
    assert status[0].key_setup_version == None
    assert status[1].key_setup_version == None
    observe_errors.flush(NoCommonVersionError)

# v0+v1+v2 clients are more interesting

def do_v0v1v2():
    return mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v0", "v1", "v2"])
def do_v1v2():
    return mock.patch("wormhole._key_setup.negotiator.KEY_SETUP_VERSIONS", ["v1", "v2"])

# One class of attack is to remove v2, in an attempt to force v1. This
# will be caught by the transcript hash divergence, regardless of
# whether the client accepts v0 or not.

@ensureDeferred
async def test_v0v1v2_remove_v2_caught(reactor, mailbox):
    status = [None, None]
    with (do_v0v1v2(), corrupt(remove=["v2"])):
        with pytest.raises(WrongPasswordError):
            await make_connection(reactor, mailbox, status)
    assert status[0].key_setup_version == "v1"
    assert status[1].key_setup_version == "v1"

# Such a client is vulnerable to downgrade all the way to v0 (by
# removing both v1 and v2).

@ensureDeferred
async def test_v0v1v2_remove_v1v2_vulnerable(reactor, mailbox):
    status = [None, None]
    with (do_v0v1v2(), corrupt(remove_all=True)):
        await make_connection(reactor, mailbox, status)
    assert status[0].key_setup_version == "v0"
    assert status[1].key_setup_version == "v0"

# a v0+v1+v2 client which rejects v0 is not vulnerable: downgrading to
# v1 is caught by the transcript hash, downgrading to v0 causes
# NoCommonVersionError

@ensureDeferred
async def test_v1v2_remove_v2_caught(reactor, mailbox):
    status = [None, None]
    with (do_v1v2(), corrupt(remove=["v2"])):
        with pytest.raises(WrongPasswordError) as err:
            await make_connection(reactor, mailbox, status)
        print()
        print("---HERE")
        print(err)
        print(err.value, type(err.value))
        print(dir(err))
    assert status[0].key_setup_version == "v1"
    assert status[1].key_setup_version == "v1"

@ensureDeferred
async def test_v1v2_remove_v1v2_no_common(reactor, mailbox, observe_errors):
    status = [None, None]
    with (do_v1v2(), corrupt(remove_all=True)):
        with pytest.raises(NoCommonVersionError) as err:
            await make_connection(reactor, mailbox, status)
        #print(err)
        assert err.value.my_versions == ["v1", "v2"]
        assert err.value.their_versions == ["v0"]
    assert status[0].key_setup_version == None
    assert status[1].key_setup_version == None
    observe_errors.flush(NoCommonVersionError)
