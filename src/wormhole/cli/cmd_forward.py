from __future__ import print_function

import hashlib
import os
import sys
import json
import itertools

import stat
import struct
import tempfile
import zipfile

import six
import msgpack
from humanize import naturalsize
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred
from twisted.internet.endpoints import serverFromString, clientFromString
from twisted.internet.protocol import Factory, Protocol
from twisted.internet.process import ProcessWriter, ProcessReader
from twisted.internet.stdio import StandardIO
from twisted.protocols.basic import LineReceiver
from twisted.python import log
from twisted.python.failure import Failure
from wormhole import __version__, create

from ..errors import TransferError, UnsendableFileError
from ..transit import TransitSender
from ..util import bytes_to_dict, bytes_to_hexstr, dict_to_bytes
from .welcome import handle_welcome

APPID = u"lothar.com/wormhole/forward"
VERIFY_TIMER = float(os.environ.get("_MAGIC_WORMHOLE_TEST_VERIFY_TIMER", 1.0))


@inlineCallbacks
def forward(args, reactor=reactor):
    """
    I implement 'wormhole forward'.
    """
    assert isinstance(args.relay_url, type(u""))
    if args.tor:
        from ..tor_manager import get_tor
        tor = yield get_tor(
            reactor,
            args.launch_tor,
            args.tor_control_port,
            timing=args.timing,
        )
    else:
        tor = None

    w = create(
        args.appid or APPID,
        args.relay_url,
        reactor,
        tor=tor,
        timing=args.timing,
        _enable_dilate=True,
    )
    if args.debug_state:
        w.debug_set_trace("forward", which=" ".join(args.debug_state), file=args.stdout)

    try:
        # if we succeed, we should close and return the w.close results
        # (which might be an error)
        res = yield _forward_loop(args, w)
        yield w.close()  # wait for ack
        returnValue(res)

    except Exception:
        # if we raise an error, we should close and then return the original
        # error (the close might give us an error, but it isn't as important
        # as the original one)
        try:
            yield w.close()  # might be an error too
        except Exception:
            pass
        f = Failure()
        returnValue(f)


class ForwardConnecter(Protocol):
    """
    Incoming connections from the other side produce this protocol.

    Forwards data to the `.other_protocol` in the Factory only after
    awaiting a single incoming length-prefixed msgpack message.

    This message tells us when the other side has successfully
    connected (or not).
    """

    def connectionMade(self):
        self._buffer = b""

    def dataReceived(self, data):
        if self._buffer is not None:
            self._buffer += data
            bsize = len(self._buffer)
            if bsize >= 2:
                msgsize, = struct.unpack("!H", self._buffer[:2])
                if bsize > msgsize + 2:
                    raise RuntimeError("leftover data in first message")
                elif bsize == msgsize + 2:
                    msg = msgpack.unpackb(self._buffer[2:2 + msgsize])
                    if not msg.get("connected", False):
                        self.transport.loseConnection()
                        raise RuntimeError("Other side failed to connect")
                    self.factory.other_proto._maybe_drain_queue()
                    self._buffer = None
            return
        else:
            self.factory.other_proto.transport.write(data)

    def connectionLost(self, reason):
        print("ForwardConnecter lost", reason)
        if self.factory.other_proto:
            self.factory.other_proto.transport.loseConnection()


class Forwarder(Protocol):
    """
    Forwards data to the `.other_protocol` in the Factory.
    """

    def connectionMade(self):
        self._buffer = b""

    def dataReceived(self, data):
        if self._buffer is not None:
            self._buffer += data
            bsize = len(self._buffer)

            if bsize >= 2:
                msgsize, = struct.unpack("!H", self._buffer[:2])
                if bsize > msgsize + 2:
                    raise RuntimeError("leftover")
                elif bsize == msgsize + 2:
                    msg = msgpack.unpackb(self._buffer[2:2 + msgsize])
                    if not msg.get("connected", False):
                        self.transport.loseConnection()
                        raise RuntimeError("no connection")
                    self.factory.other_proto._maybe_drain_queue()
                    self._buffer = None
            return
        else:
            self.factory.other_proto.transport.write(data)

    def connectionLost(self, reason):
        print("Forwarder lost", reason)


class LocalServer(Protocol):
    """
    Listen on an endpoint. On every connection: open a subchannel,
    follow the protocol from _forward_loop above (ultimately
    forwarding data).
    """

    def connectionMade(self):
        self.queue = []
        self.remote = None

        def got_proto(proto):
            proto.local = self
            self.remote = proto
            msg = msgpack.packb({
                "local-destination": self.factory.endpoint_str,
            })
            prefix = struct.pack("!H", len(msg))
            proto.transport.write(prefix + msg)
            # MUST wait for reply first -- queueing all messages
            # until then
            # XXX needs producer/consumer
        factory = Factory.forProtocol(ForwardConnecter)
        factory.other_proto = self
        d = self.factory.connect_ep.connect(factory)
        d.addCallback(got_proto)

        def err(f):
            print("BADBAD", f)
        d.addErrback(err)
        return d

    def _maybe_drain_queue(self):
        while self.queue:
            msg = self.queue.pop(0)
            self.remote.transport.write(msg)
            print("q wrote", len(msg))
        self.queue = None

    def connectionLost(self, reason):
        print("local connection lost")
#        if self.remote.transport:
#            self.remote.transport.loseConnection()

    def dataReceived(self, data):
        # XXX producer/consumer
        if self.queue is not None:
            print("queue", len(data))
            self.queue.append(data)
        else:
            self.remote.transport.write(data)
            print("wrote", len(data))


class Incoming(Protocol):
    """
    Handle an incoming Dilation subchannel. This will be from a
    listener on the other end of the wormhole.

    There is an opening message, and then we forward bytes.

    The opening message is a length-prefixed blob; the first 2
    bytes of the stream indicate the length (an unsigned short in
    network byte order).

    The message itself is msgpack-encoded.

    A single reply is produced, following the same format: 2-byte
    length prefix followed by a msgpack-encoded payload.

    The opening message contains a dict like::

        {
            "local-desination": "tcp:localhost:1234",
        }

    The "forwarding" side (i.e the one that opened the subchannel)
    MUST NOT send any data except the opening message until it
    receives a reply from this side. This side (the connecting
    side) may deny the connection for any reason (e.g. it might
    not even try, if policy says not to).

    XXX want some opt-in / permission on this side, probably? (for
    now: anything goes)
    """

    def connectionMade(self):
        print("incoming connection")
        # XXX first message should tell us where to connect, locally
        # (want some kind of opt-in on this side, probably)
        self._buffer = b""
        self._local_connection = None

    def connectionLost(self, reason):
        print("incoming connection lost")
        if self._local_connection and self._local_connection.transport:
            print("doing a lose", self._local_connection)
            self._local_connection.transport.loseConnection()

    def forward(self, data):
        print("forward {}".format(len(data)))
        self._local_connection.transport.write(data)

    @inlineCallbacks
    def _establish_local_connection(self, first_msg):
        data = msgpack.unpackb(first_msg)
        ep = clientFromString(reactor, data["local-destination"])
        print("endpoint", data["local-destination"])
        factory = Factory.forProtocol(Forwarder)
        factory.other_proto = self
        try:
            self._local_connection = yield ep.connect(factory)
        except Exception as e:
            print("BAD", e)
            self.transport.loseConnection()
            return
        # this one doesn't have to wait for an incoming message
        self._local_connection._buffer = None
        # sending-reply maybe should move somewhere else?
        # XXX another section like this: pack_netstring() or something
        msg = msgpack.packb({
            "connected": True,
        })
        prefix = struct.pack("!H", len(msg))
        self.transport.write(prefix + msg)

    def dataReceived(self, data):
        print("incoming {}".format(len(data)))
        # XXX wait, still need to buffer? .. no, we _shouldn't_
        # get data until we've got the connection -- double-check
        if self._buffer is None:
            assert self._local_connection is not None, "expected local connection by now"
            self.forward(data)

        else:
            self._buffer += data
            bsize = len(self._buffer)
            if bsize >= 2:
                expected_size, = struct.unpack("!H", self._buffer[:2])
                if bsize >= expected_size + 2:
                    first_msg = self._buffer[2:2 + expected_size]
                    # there should be no "leftover" data
                    if bsize > 2 + expected_size:
                        raise RuntimeError("protocol error: more than opening message sent")

                    d = self._establish_local_connection(
                        first_msg,
                    )
                    self._buffer = None


@inlineCallbacks
def _forward_loop(args, w):
    """
    Run the main loop of the forward:
       - perform setup (version stuff etc)
       - wait for commands (as single-line JSON messages) on stdin
       - write results to stdout (as single-line JSON messages)
       - service subchannels

    The following commands are understood:

    {
        "kind": "local",
        "endpoint": "tcp:8888",
        "local-endpoint": "tcp:localhost:8888",
    }

    This instructs us to listen on `endpoint` (any Twisted server-type
    endpoint string). On any connection to that listener, we open a
    subchannel through the wormhome and send an opening message
    (length-prefixed msgpack) like:

    {
        "local-destination": "tcp:localhost:8888",
    }

    This instructs the other end of the subchannel to open a local
    connection to the Twisted client-type endpoint `local-destination`
    .. after this, all traffic to either end is forwarded.

    Before forwarding anything, the listening end waits for permission
    to continue in a reply message (also length-prefixed msgpack)
    like:

    {
        "connected": True,
    }
    """

    welcome = yield w.get_welcome()
    print(
        json.dumps({
            "welcome": welcome
        })
    )

    if args.code:
        w.set_code(args.code)
    else:
        w.allocate_code(args.code_length)

    code = yield w.get_code()
    print(
        json.dumps({
            "kind": "wormhole-code",
            "code": code,
        })
    )

    control_ep, connect_ep, listen_ep = w.dilate()

    in_factory = Factory.forProtocol(Incoming)
    in_factory.connect_ep = connect_ep
    listen_ep.listen(in_factory)

    yield w.get_unverified_key()
    verifier_bytes = yield w.get_verifier()  # might WrongPasswordError

    if args.verify:
        raise NotImplementedError()

    @inlineCallbacks
    def _local_to_remote_forward(cmd):
        """
        Listen locally, and for each local connection create an Outgoing
        subchannel which will connect on the other end.
        """
        ep = serverFromString(reactor, cmd["endpoint"])
        print("listen endpoint", cmd["endpoint"])
        factory = Factory.forProtocol(LocalServer)
        factory.endpoint_str = cmd["local-endpoint"]
        factory.connect_ep = connect_ep
        proto = yield ep.listen(factory)

    def process_command(cmd):
        if "kind" not in cmd:
            raise ValueError("no 'kind' in command")

        return {
            # listens locally, conencts to other side
            "local": _local_to_remote_forward,

            # maybe?: asks the other side to listen, connects to us
            # "remote": _remote_to_local_forward,
        }[cmd["kind"]](cmd)

    class CommandDispatch(LineReceiver):
        """
        Wait for incoming commands (as lines of JSON) and dispatch them.
        """
        delimiter = b"\n"

        def connectionMade(self):
            print(json.dumps({
                "kind": "connected",
            }))

        def lineReceived(self, line):
            try:
                cmd = json.loads(line)
                d = process_command(cmd)
                d.addErrback(print)
                return d
            except Exception as e:
                print(f"{line.strip()}: failed: {e}")

    # arrange to read incoming commands from stdin
    x = StandardIO(CommandDispatch())
    yield Deferred()
