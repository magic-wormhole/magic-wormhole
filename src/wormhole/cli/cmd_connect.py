from __future__ import print_function

import hashlib
import os
import sys
import json
import itertools

import stat
import tempfile
import zipfile

import six
from humanize import naturalsize
from tqdm import tqdm
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred
from twisted.protocols import basic
from twisted.internet.protocol import Factory, Protocol
from twisted.internet.process import ProcessWriter, ProcessReader
from twisted.python import log
from twisted.python.failure import Failure
from wormhole import __version__, create

from ..errors import TransferError, UnsendableFileError
from ..transit import TransitSender
from ..util import bytes_to_dict, bytes_to_hexstr, dict_to_bytes
from .welcome import handle_welcome

APPID = u"lothar.com/wormhole/connect"
VERIFY_TIMER = float(os.environ.get("_MAGIC_WORMHOLE_TEST_VERIFY_TIMER", 1.0))


@inlineCallbacks
def connect(args, reactor=reactor):
    """
    I implement 'wormhole connect'.
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
        res = yield _connect_loop(args, w)
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
def _connect_loop(args, w):
    """
    Run the main loop of the connect:
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
    _next_id = itertools.count(1, 1)

    def create_subchannel_id():
        return next(_next_id)

    class SubchannelMapper:
        id_to_incoming = dict()

        def subchannel_opened(self, incoming):
            i = create_subchannel_id()
            self.id_to_incoming[i] = incoming
            return i

    mapper = SubchannelMapper()


    class Incoming(Protocol):
        """
        Wired between a local pipe and the 'incoming' side of the Dilation
        subchannel (that is, messages _from_ the other peer).

        Our end of the pipe is the 'writeable' one.
        """

        def connectionMade(self):
            self._id = mapper.subchannel_opened(self)
            # XXX parent has to open pipes? so ... we tell it a
            # subchannel opened, and it has to tell us what pipes to
            # use .. so we don't read/write data until then?
            #
            # (hmm, actually, can _we_ just open the FIFOs and tell parent?)
            print(
                json.dumps({
                    "kind": "subchannel-open",
                })
            )

        def connectionLost(self, reason):
            print(
                json.dumps({
                    "kind": "subchannel-close",
                    "id": self._id,
                    "reason": str(reason),
                })
            )
            self._id = None

        def dataReceived(self, data):
            print(
                json.dumps({
                    "kind": "subchannel-record",
                    "id": self._id,
                    "size": len(data),
                })
            )


    class Outgoing(Protocol):
        """
        Links a local pipe and the 'outgoing' side of the Dilation
        subchannel (message _to_ the other peer).

        Our end of the pipe is the 'readable' one.
        """
        # XXX how to get our pipe (thing to read)?
        def connectionMade(self):
            r, w = self.factory._file_descriptors
            print("CONN", r, w)

        def dataReceived(self, data):
            print(f"out_record: {data}")

    listen_ep.listen(Factory.forProtocol(Incoming))

    # We don't print a "waiting" message for get_unverified_key() here,
    # even though we do that in cmd_receive.py, because it's not at all
    # surprising to we waiting here for a long time. We'll sit in
    # get_unverified_key() until the receiver has typed in the code and
    # their PAKE message makes it to us.
    yield w.get_unverified_key()

    verifier_bytes = yield w.get_verifier()  # might WrongPasswordError

    if args.verify:
        raise NotImplementedError()

    ## print(f"verifier: {verifier_bytes}")

    # arrange to read incoming commands from stdin
    from twisted.internet.stdio import StandardIO
    from twisted.protocols.basic import LineReceiver

    @inlineCallbacks
    def _open_subchannel(cmd):
        print("open subchannel")
        # two pipes: one for "into the subchannel", one for "out of
        # the subchannel". We get told (by parent) which FD to open
        # for each sort of thing.
        print("open", cmd)
        in_fd = cmd["input"]
        out_fd = cmd["output"]
        factory = Factory.forProtocol(Outgoing)
        factory._file_descriptors = (in_fd, out_fd)
        proto = yield connect_ep.connect(factory)
        print(f"PROTO: {proto}")
        proto.transport.write(b'{"kind": "dummy"}\n')

    def process_command(cmd):
        print("cmd", cmd)
        if "kind" not in cmd:
            raise ValueError("no 'kind' in command")

        {
            "subchannel": _open_subchannel,
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
                process_command(cmd)
            except Exception as e:
                print(f"{line.strip()}: failed: {e}")


    x = StandardIO(CommandDispatch())
    yield Deferred()
