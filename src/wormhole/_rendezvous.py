import os
from six.moves.urllib_parse import urlparse
from attr import attrs, attrib
from attr.validators import provides, instance_of
from zope.interface import implementer
from twisted.python import log
from twisted.internet import defer, endpoints
from twisted.application import internet
from autobahn.twisted import websocket
from . import _interfaces
from .util import (bytes_to_hexstr, hexstr_to_bytes,
                   bytes_to_dict, dict_to_bytes)

class WSClient(websocket.WebSocketClientProtocol):
    def onConnect(self, response):
        # this fires during WebSocket negotiation, and isn't very useful
        # unless you want to modify the protocol settings
        #print("onConnect", response)
        pass

    def onOpen(self, *args):
        # this fires when the WebSocket is ready to go. No arguments
        #print("onOpen", args)
        #self.wormhole_open = True
        self._RC.ws_open(self)

    def onMessage(self, payload, isBinary):
        #print("onMessage")
        assert not isBinary
        self._RC.ws_message(payload)

    def onClose(self, wasClean, code, reason):
        #print("onClose")
        self._RC.ws_close(wasClean, code, reason)
        #if self.wormhole_open:
        #    self.wormhole._ws_closed(wasClean, code, reason)
        #else:
        #    # we closed before establishing a connection (onConnect) or
        #    # finishing WebSocket negotiation (onOpen): errback
        #    self.factory.d.errback(error.ConnectError(reason))

class WSFactory(websocket.WebSocketClientFactory):
    protocol = WSClient
    def __init__(self, RC, *args, **kwargs):
        websocket.WebSocketClientFactory.__init__(self, *args, **kwargs)
        self._RC = RC

    def buildProtocol(self, addr):
        proto = websocket.WebSocketClientFactory.buildProtocol(self, addr)
        proto._RC = self._RC
        #proto.wormhole_open = False
        return proto

@attrs
@implementer(_interfaces.IRendezvousConnector)
class RendezvousConnector(object):
    _url = attrib(validator=instance_of(type(u"")))
    _appid = attrib(validator=instance_of(type(u"")))
    _side = attrib(validator=instance_of(type(u"")))
    _reactor = attrib()
    _journal = attrib(validator=provides(_interfaces.IJournal))
    _timing = attrib(validator=provides(_interfaces.ITiming))

    def __init__(self):
        self._ws = None
        f = WSFactory(self, self._url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        p = urlparse(self._url)
        ep = self._make_endpoint(p.hostname, p.port or 80)
        self._connector = internet.ClientService(ep, f)

    def _make_endpoint(self, hostname, port):
        # TODO: Tor goes here
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    def wire(self, boss, mailbox, code, nameplate_lister):
        self._B = _interfaces.IBoss(boss)
        self._M = _interfaces.IMailbox(mailbox)
        self._C = _interfaces.ICode(code)
        self._NL = _interfaces.INameplateLister(nameplate_lister)

    # from Boss
    def start(self):
        self._connector.startService()

    # from Mailbox
    def tx_claim(self, nameplate):
        self._tx("claim", nameplate=nameplate)

    def tx_open(self, mailbox):
        self._tx("open", mailbox=mailbox)

    def tx_add(self, phase, body):
        assert isinstance(phase, type("")), type(phase)
        assert isinstance(body, type(b"")), type(body)
        self._tx("add", phase=phase, body=bytes_to_hexstr(body))

    def tx_release(self):
        self._tx("release")

    def tx_close(self, mood):
        self._tx("close", mood=mood)

    def stop(self):
        d = defer.maybeDeferred(self._connector.stopService)
        d.addErrback(log.err) # TODO: deliver error upstairs?
        d.addBoth(self._stopped)


    # from NameplateLister
    def tx_list(self):
        self._tx("list")

    # from Code
    def tx_allocate(self):
        self._tx("allocate")

    # from our WSClient (the WebSocket protocol)
    def ws_open(self, proto):
        self._ws = proto
        self._tx("bind", appid=self._appid, side=self._side)
        self._M.connected()
        self._NL.connected()

    def ws_message(self, payload):
        msg = bytes_to_dict(payload)
        if self.DEBUG and msg["type"]!="ack": print("DIS", msg["type"], msg)
        self._timing.add("ws_receive", _side=self._side, message=msg)
        mtype = msg["type"]
        meth = getattr(self, "_response_handle_"+mtype, None)
        if not meth:
            # make tests fail, but real application will ignore it
            log.err(ValueError("Unknown inbound message type %r" % (msg,)))
            return
        return meth(msg)

    def ws_close(self, wasClean, code, reason):
        self._ws = None
        self._M.lost()
        self._NL.lost()

    # internal
    def _stopped(self, res):
        self._M.stopped()

    def _tx(self, mtype, **kwargs):
        assert self._ws
        # msgid is used by misc/dump-timing.py to correlate our sends with
        # their receives, and vice versa. They are also correlated with the
        # ACKs we get back from the server (which we otherwise ignore). There
        # are so few messages, 16 bits is enough to be mostly-unique.
        if self.DEBUG: print("SEND", mtype)
        kwargs["id"] = bytes_to_hexstr(os.urandom(2))
        kwargs["type"] = mtype
        payload = dict_to_bytes(kwargs)
        self._timing.add("ws_send", _side=self._side, **kwargs)
        self._ws.sendMessage(payload, False)

    def _response_handle_allocated(self, msg):
        nameplate = msg["nameplate"]
        assert isinstance(nameplate, type("")), type(nameplate)
        self._C.rx_allocated(nameplate)

    def _response_handle_nameplates(self, msg):
        nameplates = msg["nameplates"]
        assert isinstance(nameplates, list), type(nameplates)
        nids = []
        for n in nameplates:
            assert isinstance(n, dict), type(n)
            nameplate_id = n["id"]
            assert isinstance(nameplate_id, type("")), type(nameplate_id)
            nids.append(nameplate_id)
        self._NL.rx_nameplates(nids)

    def _response_handle_ack(self, msg):
        pass

    def _response_handle_welcome(self, msg):
        self._B.rx_welcome(msg["welcome"])

    def _response_handle_claimed(self, msg):
        mailbox = msg["mailbox"]
        assert isinstance(mailbox, type("")), type(mailbox)
        self._M.rx_claimed(mailbox)

    def _response_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        assert isinstance(phase, type("")), type(phase)
        body = hexstr_to_bytes(msg["body"]) # bytes
        self._M.rx_message(side, phase, body)

    def _response_handle_released(self, msg):
        self._M.rx_released()

    def _response_handle_closed(self, msg):
        self._M.rx_closed()


    # record, message, payload, packet, bundle, ciphertext, plaintext
