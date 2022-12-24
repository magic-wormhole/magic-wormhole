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
from twisted.protocols import basic
from twisted.internet.endpoints import serverFromString, clientFromString
from twisted.internet.protocol import Factory, Protocol
from twisted.internet.process import ProcessWriter, ProcessReader
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
        w.debug_set_trace("send", which=" ".join(args.debug_state), file=args.stdout)

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


@inlineCallbacks
def _forward_loop(args, w):
    """
    Run the main loop of the forward:
       - perform setup (version stuff etc)
       - wait for commands (as single-line JSON messages) on stdin
       - write results to stdout (as single-line JSON messages)
       - service subchannels
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

    class ForwardConnecter(Protocol):
        """
        Forwards data to the `.other_protocol` in the Factory only after
        awaiting a single incoming length-prefixed msgpack message.

        This message tells us when the other side has successfully
        connected (or not).
        """

        def connectionMade(self):
            print("fwd conn")
            self._buffer = b""

        def dataReceived(self, data):
            print("fwd incoming {}".format(len(data)))
            if self._buffer is not None:
                self._buffer += data
                print(self._buffer)
                bsize = len(self._buffer)
                print("bsize", bsize)
                if bsize >= 2:
                    msgsize, = struct.unpack("!H", self._buffer[:2])
                    print("sze", msgsize)
                    if bsize > msgsize + 2:
                        raise RuntimeError("leftover")
                    elif bsize == msgsize + 2:
                        msg = msgpack.unpackb(self._buffer[2:2 + msgsize])
                        print("MSG", msg)
                        if not msg.get("connected", False):
                            print(dir(self.transport))
                            self.transport.loseConnection()
                            raise RuntimeError("Other side failed to connect")
                        self.factory.server_proto._maybe_drain_queue()
                        self._buffer = None
                    else:
                        print("need more", msgsize, bsize)
                return
            else:
                print("fwd {} {}".format(len(data), dir(self.factory)))
                self.factory.server_proto.transport.write(data)
                print(data)


    class Forwarder(Protocol):
        """
        Forwards data to the `.other_protocol` in the Factory.
        """

        def connectionMade(self):
            print("fwd conn")
            self._buffer = b""

        def dataReceived(self, data):
            print("fwd incoming {}".format(len(data)))
            if self._buffer is not None:
                self._buffer += data
                print(self._buffer)
                bsize = len(self._buffer)
                print("bsize", bsize)
                if bsize >= 2:
                    msgsize, = struct.unpack("!H", self._buffer[:2])
                    print("sze", msgsize)
                    if bsize > msgsize + 2:
                        raise RuntimeError("leftover")
                    elif bsize == msgsize + 2:
                        msg = msgpack.unpackb(self._buffer[2:2 + msgsize])
                        print("MSG", msg)
                        self.factory.server_proto._maybe_drain_queue()
                        if not msg.get("connected", False):
                            raise RuntimeError("no connection")
                        self._buffer = None
                    else:
                        print("need more", msgsize, bsize)
                return
            else:
                print("fwd {} {}".format(len(data), dir(self.factory)))
                self.factory.server_proto.transport.write(data)
                print(data)


    class LocalServer(Protocol):
        """
        """

        def connectionMade(self):
            print("local connection")
            print(self.factory)
            print(self.factory.endpoint_str)
            self.queue = []
            self.remote = None

            def got_proto(proto):
                print("PROTO", proto)
                proto.local = self
                self.remote = proto
                msg = msgpack.packb({
                    "local-destination": self.factory.endpoint_str,
                })
                prefix = struct.pack("!H", len(msg))
                proto.transport.write(prefix + msg)
                print("WROTE", prefix + msg)
                # MUST wait for reply first
            factory = Factory.forProtocol(Forwarder)
            factory.server_proto = self
            d = connect_ep.connect(factory)
            d.addCallback(got_proto)
            d.addErrback(print)
            return d

        def _maybe_drain_queue(self):
            while self.queue:
                msg = self.queue.pop(0)
                self.remote.transport.write(msg)
                print("wrote", len(msg))
                print(msg)
            self.queue = None

        def connectionLost(self, reason):
            print("local connection lost")

        def dataReceived(self, data):
            print("local {}b".format(len(data)))
            if self.queue is not None:
                print("queue", len(data))
                self.queue.append(data)
            else:
                self.remote.transport.write(data)
                print("wrote", len(data))
                print(data)


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

        def forward(self, data):
            print("forward {}".format(len(data)))
            self._local_connection.transport.write(data)

        @inlineCallbacks
        def _establish_local_connection(self, first_msg):
            data = msgpack.unpackb(first_msg)
            print("local con", data)
            ep = clientFromString(reactor, data["local-destination"])
            print("ep", ep)
            factory = Factory.forProtocol(Forwarder)
            factory.server_proto = self
            d = ep.connect(factory)
            print("DDD", d)
            self._local_connection = yield d
            print("conn", self._local_connection)
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
            print("incoming {}b".format(len(data)))
            # XXX wait, still need to buffer? .. no, we _shouldn't_
            # get data until we've got the connection -- double-check
            if self._buffer is None:
                print(data)
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


    class Outgoing(Protocol):
        """
        """
        def connectionMade(self):
            print("outgoing conn")

        def dataReceived(self, data):
            print(f"out_record: {data}")

    listen_ep.listen(Factory.forProtocol(Incoming))

    yield w.get_unverified_key()
    verifier_bytes = yield w.get_verifier()  # might WrongPasswordError

    if args.verify:
        raise NotImplementedError()

    # arrange to read incoming commands from stdin
    from twisted.internet.stdio import StandardIO
    from twisted.protocols.basic import LineReceiver

    @inlineCallbacks
    def _local_to_remote_forward(cmd):
        """
        Listen locally, and for each local connection create an Outgoing
        subchannel which will connect on the other end.
        """
        print("local forward", cmd)
        ep = serverFromString(reactor, cmd["endpoint"])
        print("ep", ep)
        factory = Factory.forProtocol(LocalServer)
        factory.endpoint_str = cmd["local-endpoint"]
        proto = yield ep.listen(factory)
        print(f"PROTO: {proto}")
        ##proto.transport.write(b'{"kind": "dummy"}\n')

    def process_command(cmd):
        print("cmd", cmd)
        if "kind" not in cmd:
            raise ValueError("no 'kind' in command")

        return {
            # listens locally, conencts to other side
            "local": _local_to_remote_forward,

            # asks the other side to listen, connects to us
            # "remote": _remote_to_local_forward,
        }[cmd["kind"]](cmd)

    class CommandDispatch(LineReceiver):
        delimiter = b"\n"
        def connectionMade(self):
            print(json.dumps({
                "kind": "connected",
            }))

        def lineReceived(self, line):
            try:
                cmd = json.loads(line)
                d = process_command(cmd)
                print("ZZZ", d)
                d.addErrback(print)
                return d
            except Exception as e:
                print(f"{line.strip()}: failed: {e}")


    x = StandardIO(CommandDispatch())
    yield Deferred()
