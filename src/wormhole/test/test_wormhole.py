from __future__ import print_function
import json
import mock
from twisted.trial import unittest
from twisted.internet import reactor
from twisted.internet.defer import gatherResults, inlineCallbacks
#from ..twisted.transcribe import (wormhole, wormhole_from_serialized,
#                                  UsageError, WrongPasswordError)
#from .common import ServerBase
from ..wormhole import _Wormhole, _WelcomeHandler
from ..timing import DebugTiming

APPID = u"appid"

class MockWebSocket:
    def __init__(self):
        self._payloads = []
    def sendMessage(self, payload, is_binary):
        assert not is_binary
        self._payloads.append(payload)

    def outbound(self):
        out = []
        while self._payloads:
            p = self._payloads.pop(0)
            out.append(json.loads(p.decode("utf-8")))
        return out

def response(w, **kwargs):
    payload = json.dumps(kwargs).encode("utf-8")
    w._ws_dispatch_response(payload)

class Welcome(unittest.TestCase):
    def test_no_current_version(self):
        # WelcomeHandler should tolerate lack of ["current_version"]
        w = _WelcomeHandler(u"relay_url", u"current_version")
        w.handle_welcome({})


class Basic(unittest.TestCase):
    def test_create(self):
        w = _Wormhole(APPID, u"relay_url", reactor, None, None)

    def test_basic(self):
        # We don't call w._start(), so this doesn't create a WebSocket
        # connection. We provide a mock connection instead.
        timing = DebugTiming()
        with mock.patch("wormhole.wormhole._WelcomeHandler") as whc:
            w = _Wormhole(APPID, u"relay_url", reactor, None, timing)
        wh = whc.return_value
        #w._welcomer = mock.Mock()
        # w._connect = lambda self: None
        # w._event_connected(mock_ws)
        # w._event_ws_opened()
        # w._ws_dispatch_response(payload)
        self.assertEqual(w._ws_url, u"relay_url")
        ws = MockWebSocket()
        w._event_connected(ws)
        out = ws.outbound()
        self.assertEqual(len(out), 0)

        w._event_ws_opened(None)
        out = ws.outbound()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], u"bind")
        self.assertEqual(out[0]["appid"], APPID)
        self.assertEqual(out[0]["side"], w._side)
        self.assertIn(u"id", out[0])

        # WelcomeHandler should get called upon 'welcome' response
        WELCOME = {u"foo": u"bar"}
        response(w, type="welcome", welcome=WELCOME)
        self.assertEqual(wh.mock_calls, [mock.call.handle_welcome(WELCOME)])

