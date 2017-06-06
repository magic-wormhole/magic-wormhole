from __future__ import print_function, absolute_import, unicode_literals
import os
from six.moves.urllib_parse import urlparse
from attr import attrs, attrib
from attr.validators import provides, instance_of, optional
from zope.interface import implementer
from twisted.python import log
from twisted.internet import defer, endpoints
from twisted.application import internet
from autobahn.twisted import websocket
from . import _interfaces, errors
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
        assert not isBinary
        try:
            self._RC.ws_message(payload)
        except:
            from twisted.python.failure import Failure
            print("LOGGING", Failure())
            log.err()
            raise

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
    _tor = attrib(validator=optional(provides(_interfaces.ITorManager)))
    _timing = attrib(validator=provides(_interfaces.ITiming))

    def __attrs_post_init__(self):
        self._trace = None
        self._ws = None
        f = WSFactory(self, self._url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        p = urlparse(self._url)
        ep = self._make_endpoint(p.hostname, p.port or 80)
        # TODO: change/wrap ClientService to fail if the first attempt fails
        self._connector = internet.ClientService(ep, f)
        self._debug_record_inbound_f = None

    def set_trace(self, f):
        self._trace = f
    def _debug(self, what):
        if self._trace:
            self._trace(old_state="", input=what, new_state="")

    def _make_endpoint(self, hostname, port):
        if self._tor:
            # TODO: when we enable TLS, maybe add tls=True here
            return self._tor.stream_via(hostname, port)
        return endpoints.HostnameEndpoint(self._reactor, hostname, port)

    def wire(self, boss, nameplate, mailbox, allocator, lister, terminator):
        self._B = _interfaces.IBoss(boss)
        self._N = _interfaces.INameplate(nameplate)
        self._M = _interfaces.IMailbox(mailbox)
        self._A = _interfaces.IAllocator(allocator)
        self._L = _interfaces.ILister(lister)
        self._T = _interfaces.ITerminator(terminator)

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

    def tx_release(self, nameplate):
        self._tx("release", nameplate=nameplate)

    def tx_close(self, mailbox, mood):
        self._tx("close", mailbox=mailbox, mood=mood)

    def stop(self):
        # ClientService.stopService is defined to "Stop attempting to
        # reconnect and close any existing connections"
        d = defer.maybeDeferred(self._connector.stopService)
        d.addErrback(log.err) # TODO: deliver error upstairs?
        d.addBoth(self._stopped)


    # from Lister
    def tx_list(self):
        self._tx("list")

    # from Code
    def tx_allocate(self):
        self._tx("allocate")

    # from our WSClient (the WebSocket protocol)
    def ws_open(self, proto):
        self._debug("R.connected")
        self._ws = proto
        try:
            #self._tx("bind", appid=self._appid, side=self._side)
            self._tx("abilities", mitigation_token=1)
            self._N.connected()
            self._M.connected()
            self._L.connected()
            self._A.connected()
        except Exception as e:
            self._B.error(e)
            raise
        self._debug("R.connected finished notifications")

    def ws_message(self, payload):
        msg = bytes_to_dict(payload)
        if msg["type"] != "ack":
                self._debug("R.rx(%s %s%s)" %
                            (msg["type"], msg.get("phase",""),
                        "[mine]" if msg.get("side","") == self._side else "",
                             ))

        self._timing.add("ws_receive", _side=self._side, message=msg)
        if self._debug_record_inbound_f:
            self._debug_record_inbound_f(msg)
        mtype = msg["type"]
        meth = getattr(self, "_response_handle_"+mtype, None)
        if not meth:
            # make tests fail, but real application will ignore it
            log.err(errors._UnknownMessageTypeError("Unknown inbound message type %r" % (msg,)))
            return
        try:
            return meth(msg)
        except Exception as e:
            log.err(e)
            self._B.error(e)
            raise

    def ws_close(self, wasClean, code, reason):
        self._debug("R.lost")
        self._ws = None
        self._N.lost()
        self._M.lost()
        self._L.lost()
        self._A.lost()

    # internal
    def _stopped(self, res):
        self._T.stopped()

    def _tx(self, mtype, **kwargs):
        assert self._ws
        # msgid is used by misc/dump-timing.py to correlate our sends with
        # their receives, and vice versa. They are also correlated with the
        # ACKs we get back from the server (which we otherwise ignore). There
        # are so few messages, 16 bits is enough to be mostly-unique.
        kwargs["id"] = bytes_to_hexstr(os.urandom(2))
        kwargs["type"] = mtype
        self._debug("R.tx(%s %s)" % (mtype.upper(), kwargs.get("phase", "")))
        payload = dict_to_bytes(kwargs)
        self._timing.add("ws_send", _side=self._side, **kwargs)
        self._ws.sendMessage(payload, False)

    def _response_handle_allocated(self, msg):
        nameplate = msg["nameplate"]
        assert isinstance(nameplate, type("")), type(nameplate)
        self._A.rx_allocated(nameplate)

    def _response_handle_nameplates(self, msg):
        # we get list of {id: ID}, with maybe more attributes in the future
        nameplates = msg["nameplates"]
        assert isinstance(nameplates, list), type(nameplates)
        nids = set()
        for n in nameplates:
            assert isinstance(n, dict), type(n)
            nameplate_id = n["id"]
            assert isinstance(nameplate_id, type("")), type(nameplate_id)
            nids.add(nameplate_id)
        # deliver a set of nameplate ids
        self._L.rx_nameplates(nids)

    def _response_handle_ack(self, msg):
        print("ACK", msg)
        pass

    def _response_handle_error(self, msg):
        # the server sent us a type=error. Most cases are due to our mistakes
        # (malformed protocol messages, sending things in the wrong order),
        # but it can also result from CrowdedError (more than two clients
        # using the same channel).
        err = msg["error"]
        orig = msg["orig"]
        self._B.rx_error(err, orig)

    def _response_handle_welcome(self, msg):
        self._B.rx_welcome(msg["welcome"])

    def _response_handle_granted(self, msg):
        self._tx("bind", appid=self._appid, side=self._side)
        self._B.rx_granted(msg)

    def _response_handle_claimed(self, msg):
        mailbox = msg["mailbox"]
        assert isinstance(mailbox, type("")), type(mailbox)
        self._N.rx_claimed(mailbox)

    def _response_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        assert isinstance(phase, type("")), type(phase)
        body = hexstr_to_bytes(msg["body"]) # bytes
        self._M.rx_message(side, phase, body)

    def _response_handle_released(self, msg):
        self._N.rx_released()

    def _response_handle_closed(self, msg):
        self._M.rx_closed()


    # record, message, payload, packet, bundle, ciphertext, plaintext
