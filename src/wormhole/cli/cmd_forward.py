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
from functools import partial

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


def _sequential_id():
    """
    Yield a stream of IDs, starting at 1
    """
    next_id = 0
    while True:
        next_id += 1
        yield next_id


allocate_connection_id = partial(next, _sequential_id())


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
        # print("ForwardConnecter lost", reason)
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
            max_noise = 65000
            while len(data):
                d = data[:max_noise]
                data = data[max_noise:]
                self.factory.other_proto.transport.write(d)

    def connectionLost(self, reason):
        if self.factory.other_proto:
            self.factory.other_proto.transport.loseConnection()

class LocalServer(Protocol):
    """
    Listen on an endpoint. On every connection: open a subchannel,
    follow the protocol from _forward_loop above (ultimately
    forwarding data).
    """

    def connectionMade(self):
        self.queue = []
        self.remote = None
        self._conn_id = allocate_connection_id()

        def got_proto(proto):
            proto.local = self
            self.remote = proto
            msg = msgpack.packb({
                "local-destination": self.factory.endpoint_str,
            })
            prefix = struct.pack("!H", len(msg))
            proto.transport.write(prefix + msg)

            print(json.dumps({
                "kind": "local-connection",
                "id": self._conn_id,
                "remote": str(proto.transport) + str(dir(proto.transport)),
            }))

            # MUST wait for reply first -- queueing all data until
            # then
            self.transport.stopProducing()
        factory = Factory.forProtocol(ForwardConnecter)
        factory.other_proto = self
        d = self.factory.connect_ep.connect(factory)
        d.addCallback(got_proto)

        def err(f):
            print(json.dumps({
                "kind": "error",
                "id": self._conn_id,
                "message": str(f.value),
            }))
        d.addErrback(err)
        return d

    def _maybe_drain_queue(self):
        while self.queue:
            msg = self.queue.pop(0)
            self.remote.transport.write(msg)
        self.queue = None

    def connectionLost(self, reason):
        pass # print("local connection lost")

    def dataReceived(self, data):
        print("DING", type(data), len(data))
        # XXX FIXME if len(data) >= 65535 must split "because noise"
        # -- handle in Dilation code?

        # XXX producer/consumer
        max_noise = 65000
        while len(data):
            d = data[:max_noise]
            data = data[max_noise:]

            if self.queue is not None:
                self.queue.append(d)
            else:
                self.remote.transport.write(d)


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
        self._conn_id = allocate_connection_id()
        print(json.dumps({
            "kind": "incoming-connect",
            "id": self._conn_id,
        }))
        # XXX first message should tell us where to connect, locally
        # (want some kind of opt-in on this side, probably)
        self._buffer = b""
        self._local_connection = None

    def connectionLost(self, reason):
        print(json.dumps({
            "kind": "incoming-lost",
            "id": self._conn_id,
        }))
        if self._local_connection and self._local_connection.transport:
            self._local_connection.transport.loseConnection()

    def forward(self, data):
        print("FORWARD", type(data), len(data))
        print(json.dumps({
            "kind": "forward-bytes",
            "id": self._conn_id,
            "bytes": len(data),
        }))

        # XXX handle in Dilation? or something?
        max_noise = 65000
        while len(data):
            d = data[:max_noise]
            data = data[max_noise:]
            self._local_connection.transport.write(d)

    @inlineCallbacks
    def _establish_local_connection(self, first_msg):
        data = msgpack.unpackb(first_msg)
        ep = clientFromString(reactor, data["local-destination"])
        print(json.dumps({
            "kind": "connect-local",
            "id": self._conn_id,
            "endpoint": data["local-destination"],
        }))
        factory = Factory.forProtocol(Forwarder)
        factory.other_proto = self
        try:
            self._local_connection = yield ep.connect(factory)
        except Exception as e:
            print(json.dumps({
                "kind": "error",
                "id": self._conn_id,
                "message": str(e),
            }))
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
        # we _should_ get only enough data to comprise the first
        # message, then we send a reply, and only then should the
        # other side send us more data ... XXX so we need to produce
        # an error if we get any data between "we got the message" and
        # our reply is sent.

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
        factory = Factory.forProtocol(LocalServer)
        factory.endpoint_str = cmd["local-endpoint"]
        factory.connect_ep = connect_ep
        proto = yield ep.listen(factory)
        print(json.dumps({
            "kind": "listening",
            "endpoint": cmd["local-endpoint"],
        }))

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
