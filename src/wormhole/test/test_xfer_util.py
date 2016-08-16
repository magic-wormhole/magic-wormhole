from twisted.trial import unittest
from twisted.internet import reactor, defer
from twisted.internet.defer import inlineCallbacks
from .. import xfer_util
from .common import ServerBase

APPID = u"appid"

class Xfer(ServerBase, unittest.TestCase):
    @inlineCallbacks
    def test_xfer(self):
        code = u"1-code"
        data = u"data"
        d1 = xfer_util.send(reactor, APPID, self.relayurl, data, code)
        d2 = xfer_util.receive(reactor, APPID, self.relayurl, code)
        send_result = yield d1
        receive_result = yield d2
        self.assertEqual(send_result, None)
        self.assertEqual(receive_result, data)

    @inlineCallbacks
    def test_on_code(self):
        code = u"1-code"
        data = u"data"
        send_code = []
        receive_code = []
        d1 = xfer_util.send(reactor, APPID, self.relayurl, data, code,
                            on_code=send_code.append)
        d2 = xfer_util.receive(reactor, APPID, self.relayurl, code,
                               on_code=receive_code.append)
        send_result = yield d1
        receive_result = yield d2
        self.assertEqual(send_code, [code])
        self.assertEqual(receive_code, [code])
        self.assertEqual(send_result, None)
        self.assertEqual(receive_result, data)

    @inlineCallbacks
    def test_make_code(self):
        data = u"data"
        got_code = defer.Deferred()
        d1 = xfer_util.send(reactor, APPID, self.relayurl, data, code=None,
                            on_code=got_code.callback)
        code = yield got_code
        d2 = xfer_util.receive(reactor, APPID, self.relayurl, code)
        send_result = yield d1
        receive_result = yield d2
        self.assertEqual(send_result, None)
        self.assertEqual(receive_result, data)
