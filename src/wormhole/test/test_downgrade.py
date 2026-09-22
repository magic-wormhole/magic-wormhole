from unittest import mock
from pytest_twisted import ensureDeferred

from .. import wormhole
from .._key_setup.negotiator import KEY_SETUP_VERSIONS, KEY_SETUP_CONSTRUCTORS


# Any client which accepts the v0 protocol is vulnerable to a
# downgrade attack, because v0 did not include a hash of the
# transcript (specifically the "my_key_setup_versions"
# advertisement). Demonstrate that v0-capable clients are vulnerable,
# and v0-incapable clients are not.

APPID = "appid"


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
