from twisted.internet import defer, reactor
from twisted.internet.defer import inlineCallbacks
from twisted.trial import unittest

from .. import xfer_util
from .common import ServerBase

APPID = u"appid"

import pytest
import pytest_twisted
import hypothesis.strategies as st
from hypothesis import given, assume


@given(
    password=st.text(
        alphabet=st.characters(exclude_categories=["Zs"]),
    ),
    nameplate=st.integers(min_value=1, max_value=2000),
    data=st.text(),
)
@pytest_twisted.ensureDeferred
async def test_xfer(password, nameplate, data, mailbox):
    code = f"{nameplate}-{password}"
    d1 = xfer_util.send(reactor, APPID, mailbox, data, code)
    d2 = xfer_util.receive(reactor, APPID, mailbox, code)
    send_result = await d1
    receive_result = await d2

    assert send_result is None
    assert receive_result == data
