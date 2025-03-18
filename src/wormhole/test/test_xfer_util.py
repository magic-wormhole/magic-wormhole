from twisted.internet import defer, reactor

from .. import xfer_util

import pytest_twisted
import hypothesis.strategies as st
from hypothesis import given, settings

APPID = u"appid"


@given(
    password=st.text(
        alphabet=st.characters(exclude_categories=["Zs", "Cs"]),
    ),
    nameplate=st.integers(min_value=1, max_value=2000),
    data=st.text(),
)
@pytest_twisted.ensureDeferred
@settings(deadline=None)
async def test_xfer(mailbox, password, nameplate, data):
    code = f"{nameplate}-{password}"
    send_result, receive_result = await defer.gatherResults([
        xfer_util.send(reactor, APPID, mailbox, data, code),
        xfer_util.receive(reactor, APPID, mailbox, code),
    ])
    assert send_result is None
    assert receive_result == data


@pytest_twisted.ensureDeferred
async def test_on_code(mailbox):
    code = u"1-code"
    data = u"data"
    send_code = []
    receive_code = []
    d1 = xfer_util.send(
        reactor,
        APPID,
        mailbox,
        data,
        code,
        on_code=send_code.append)
    d2 = xfer_util.receive(
        reactor, APPID, mailbox, code, on_code=receive_code.append)
    send_result = await d1
    receive_result = await d2
    assert send_code == [code]
    assert receive_code == [code]
    assert send_result is None
    assert receive_result == data


@pytest_twisted.ensureDeferred
async def test_make_code(mailbox):
    data = u"data"
    got_code = defer.Deferred()
    d1 = xfer_util.send(
        reactor,
        APPID,
        mailbox,
        data,
        code=None,
        on_code=got_code.callback)
    code = await got_code
    d2 = xfer_util.receive(reactor, APPID, mailbox, code)
    send_result = await d1
    receive_result = await d2
    assert send_result is None
    assert receive_result == data
