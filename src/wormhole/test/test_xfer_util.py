from twisted.internet import defer, reactor
from twisted.internet.defer import inlineCallbacks
from twisted.trial import unittest

from .. import xfer_util
from .common import ServerBase

import pytest_twisted

APPID = u"appid"


@pytest_twisted.ensureDeferred
async def test_xfer(mailbox):
    code = u"1-code"
    data = u"data"
    d1 = xfer_util.send(reactor, APPID, mailbox, data, code)
    d2 = xfer_util.receive(reactor, APPID, mailbox, code)
    send_result = await d1
    receive_result = await d2
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
