import os
import base64
import hashlib
from datetime import datetime
from urllib.parse import urlparse
from attr import attrs, attrib
from attr.validators import instance_of, optional
from zope.interface import implementer
from twisted.python import log
from twisted.internet import defer, endpoints, task, threads
from twisted.application import internet
from autobahn.twisted import websocket
from . import _interfaces, errors
from .util import (bytes_to_hexstr, hexstr_to_bytes, bytes_to_dict,
                   dict_to_bytes, provides)


class WSClient(websocket.WebSocketClientProtocol):
    def onConnect(self, response):
        # this fires during WebSocket negotiation, and isn't very useful
        # unless you want to modify the protocol settings
        # print("onConnect", response)
        pass

    def onOpen(self, *args):
        # this fires when the WebSocket is ready to go. No arguments
        # print("onOpen", args)
        # self.wormhole_open = True
        self._RC.ws_open(self)

    def onMessage(self, payload, isBinary):
        assert not isBinary
        try:
            self._RC.ws_message(payload)
        except Exception:
            from twisted.python.failure import Failure
            print("LOGGING", Failure())
            log.err()
            raise

    def onClose(self, wasClean, code, reason):
        # print("onClose")
        self._RC.ws_close(wasClean, code, reason)
        # if self.wormhole_open:
        #     self.wormhole._ws_closed(wasClean, code, reason)
        # else:
        #     # we closed before establishing a connection (onConnect) or
        #     # finishing WebSocket negotiation (onOpen): errback
        #     self.factory.d.errback(error.ConnectError(reason))


class WSFactory(websocket.WebSocketClientFactory):
    protocol = WSClient

    def __init__(self, RC, *args, **kwargs):
        websocket.WebSocketClientFactory.__init__(self, *args, **kwargs)
        self._RC = RC

    def buildProtocol(self, addr):
        proto = websocket.WebSocketClientFactory.buildProtocol(self, addr)
        proto._RC = self._RC
        # proto.wormhole_open = False
        return proto


def mint_hashcash(bits, resource):
    """
    Create a new hashcase-format string with `bits` of hardness tied
    to `resource` (an arbitrary string).

    Hashcash strings look like:

    `1:6:210623:arbitrary string::9WrCxB1SdCBOM3i5:000005`
    """
    counter = 0
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    while True:
        attempt = "1:{}:{}:{}::{}:{}".format(
            bits,
            timestamp,
            resource,
            base64.b64encode(os.urandom(12)),  # can we move this out of loop?
            counter,
        )
        if valid_hashcash(attempt, bits):
            return attempt
        counter +=1


def valid_hashcash(stamp, bits):
    """
    :returns: True if `stamp` is a valid hashcash string with `bits`
    hardness.
    """
    h = hashlib.sha1()
    h.update(stamp.encode("utf8"))
    return leading_bits(h.digest(), bits)


def leading_bits(data, required_bits):
    """
    :returns: True IFF the bytestring `data` has at least
        `required_bits` leading bits that are 0.
    """
    bits = 0
    for byte in data:
        if bits >= required_bits:
            return True
        if required_bits - bits > 8:
            if byte == 0:
                bits += 8
            else:
                return False
        else:
            mask = 1 << 7
            while mask:
                if byte & mask:
                    return False
                bits += 1
                mask = mask >> 1
                if bits >= required_bits:
                    return True


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
    _client_version = attrib(validator=instance_of(tuple))

    def __attrs_post_init__(self):
        self._have_made_a_successful_connection = False
        self._stopping = False

        self._trace = None
        self._ws = None
        f = WSFactory(self, self._url)
        f.setProtocolOptions(autoPingInterval=60, autoPingTimeout=600)
        ep = self._make_endpoint(self._url)
        self._connector = internet.ClientService(ep, f)
        faf = None if self._have_made_a_successful_connection else 1
        d = self._connector.whenConnected(failAfterFailures=faf)
        # if the initial connection fails, signal an error and shut down. do
        # this in a different reactor turn to avoid some hazards
        d.addBoth(lambda res: task.deferLater(self._reactor, 0.0, lambda: res))
        # TODO: use EventualQueue
        d.addErrback(self._initial_connection_failed)
        self._debug_record_inbound_f = None

    def set_trace(self, f):
        self._trace = f

    def _debug(self, what):
        if self._trace:
            self._trace(old_state="", input=what, new_state="")

    def _make_endpoint(self, url):
        p = urlparse(url)
        tls = (p.scheme == "wss")
        port = p.port or (443 if tls else 80)
        if self._tor:
            return self._tor.stream_via(p.hostname, port, tls=tls)
        if tls:
            return endpoints.clientFromString(self._reactor,
                                              "tls:%s:%s" % (p.hostname, port))
        return endpoints.HostnameEndpoint(self._reactor, p.hostname, port)

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
        self._stopping = True  # to catch _initial_connection_failed error
        d = defer.maybeDeferred(self._connector.stopService)
        # ClientService.stopService always fires with None, even if the
        # initial connection failed, so log.err just in case
        d.addErrback(log.err)
        d.addBoth(self._stopped)

    # from Lister
    def tx_list(self):
        self._tx("list")

    # from Code
    def tx_allocate(self):
        self._tx("allocate")

    # from our ClientService
    def _initial_connection_failed(self, f):
        if not self._stopping:
            sce = errors.ServerConnectionError(self._url, f.value)
            d = defer.maybeDeferred(self._connector.stopService)
            # this should happen right away: the ClientService ought to be in
            # the "_waiting" state, and everything in the _waiting.stop
            # transition is immediate
            d.addErrback(log.err)  # just in case something goes wrong
            d.addCallback(lambda _: self._B.error(sce))

    # from our WSClient (the WebSocket protocol)
    def ws_open(self, proto):
        self._debug("R.connected")
        self._have_made_a_successful_connection = True
        self._ws = proto

    # from _boss machine, if hashcash permission is requried
    def _send_hashcash_then_bind(self, params):

        bits = params["bits"]
        resource = params["resource"]
        # the mint function is synchronous and is designed to take a
        # bit of time..
        hashcash_d = threads.deferToThread(mint_hashcash, bits, resource)

        def got_cash(hashcash):
            self._tx("submit-permissions", method="hashcash", stamp=hashcash)
            self._send_bind()
        hashcash_d.addCallback(got_cash)

    # from _boss machine, after it's done any permissions dancing
    def _send_bind(self):
        try:
            self._tx(
                "bind",
                appid=self._appid,
                side=self._side,
                client_version=self._client_version)
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
            self._debug("R.rx(%s %s%s)" % (
                msg["type"],
                msg.get("phase", ""),
                "[mine]" if msg.get("side", "") == self._side else "",
            ))

        self._timing.add("ws_receive", _side=self._side, message=msg)
        if self._debug_record_inbound_f:
            self._debug_record_inbound_f(msg)
        mtype = msg["type"]
        meth = getattr(self, "_response_handle_" + mtype, None)
        if not meth:
            # make tests fail, but real application will ignore it
            log.err(
                errors._UnknownMessageTypeError(
                    "Unknown inbound message type %r" % (msg, )))
            return
        try:
            return meth(msg)
        except Exception as e:
            log.err(e)
            self._B.error(e)
            raise

    def ws_close(self, wasClean, code, reason):
        self._debug("R.lost")
        was_open = bool(self._ws)
        self._ws = None
        # when Autobahn connects to a non-websocket server, it gets a
        # CLOSE_STATUS_CODE_ABNORMAL_CLOSE, and delivers onClose() without
        # ever calling onOpen first. This confuses our state machines, so
        # avoid telling them we've lost the connection unless we'd previously
        # told them we'd connected.
        if was_open:
            self._N.lost()
            self._M.lost()
            self._L.lost()
            self._A.lost()

        # and if this happens on the very first connection, then we treat it
        # as a failed initial connection, even though ClientService didn't
        # notice it. There's a Twisted ticket (#8375) about giving
        # ClientService an extra setup function to use, so it can tell
        # whether post-connection negotiation was successful or not, and
        # restart the process if it fails. That would be useful here, so that
        # failAfterFailures=1 would do the right thing if the initial TCP
        # connection succeeds but the first WebSocket negotiation fails.
        if not self._have_made_a_successful_connection:
            # shut down the ClientService, which currently thinks it has a
            # valid connection
            sce = errors.ServerConnectionError(self._url, reason)
            d = defer.maybeDeferred(self._connector.stopService)
            d.addErrback(log.err)  # just in case something goes wrong
            # tell the Boss to quit and inform the user
            d.addCallback(lambda _: self._B.error(sce))

    # internal
    def _stopped(self, res):
        self._T.stoppedRC()

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

    def _response_handle_claimed(self, msg):
        mailbox = msg["mailbox"]
        assert isinstance(mailbox, type("")), type(mailbox)
        self._N.rx_claimed(mailbox)

    def _response_handle_message(self, msg):
        side = msg["side"]
        phase = msg["phase"]
        assert isinstance(phase, type("")), type(phase)
        body = hexstr_to_bytes(msg["body"])  # bytes
        self._M.rx_message(side, phase, body)

    def _response_handle_released(self, msg):
        self._N.rx_released()

    def _response_handle_closed(self, msg):
        self._M.rx_closed()

    # record, message, payload, packet, bundle, ciphertext, plaintext
