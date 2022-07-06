#
# start a child with paired AF_UNIX sockets so it can send new
# file-descriptors down it
#
# the file-descriptors it sends are os.pipe() pairs
#
### NOTES
#
# so, "fer realz" what we want is:
#
# - still need this unix-socket that sends FDs between procs
#
# - "client" proc gets passed this as part of spawn, on FD 3
# - example can be sync, use sendmsg() (or recvmsg())
# - _one_ side should always create the pipes, and send them over the unix-socket
# - who creates pipes?
#   outgoing connection, e.g. {"kind": "subchannel", ...} the 
# "wormhole connect" side manages 

import os
import sys
import json
import time
import struct
import subprocess
from socket import SOL_SOCKET, socketpair, socket, AF_UNIX, SOCK_STREAM

from twisted.internet.task import react, deferLater
from twisted.internet.defer import Deferred
from twisted.internet.error import ProcessDone
from twisted.internet.fdesc import setNonBlocking
from twisted.python.sendmsg import SCM_RIGHTS, recvmsg, sendmsg
from twisted.internet.protocol import ProcessProtocol, Protocol, Factory
from twisted.internet.interfaces import IFileDescriptorReceiver
from zope.interface import implementer


def child():
    """
    Run the child process, synchronously.

    In fd 3 is our shared unix-socket over which we send file-descriptors
    """
    # fd 3 is our UNIX socket to send other FDs down
    child_sock = socket(family=AF_UNIX, type=SOCK_STREAM, proto=0, fileno=3)

    # so, do we want "wormhole connect" to be the parent? or the child?
    #
    # -> lets say that "wormhole connect" is the parent and launches
    #    this as "the experimental thing that does one end of an
    #    application Dilation protocol"
    #
    # so, its interface is:
    #   - gets a unix-socket on fd 3, which is an AF_UNIX, SOCK_STREAM
    #   - to open subchannel: make two pipes, send over ^ (with "open subchannel" message? or on stdin?)
    #   - when subchannel opens: tell this child "make me pipes", send message over ^
    #
    # "this thing" always opens pipes.
    # so if _this_ thing wants to open a subchannel:
    #     - make pipes
    #     - {"subchannel-pipes": "fdsa", "read-fd": int, "write-fd": int}
    #     - send on stdout: {"kind": "open-subchannel", "id": fdsa"}
    #     - read/write pipes
    #     - closing pipes closes subchannel?
    #
    # when the other side opens a subchannel:
    #     - recv on stdin: {"kind": "subchannel-opened", "id": "asdf"}
    #     - make pipes
    #     - {"subchannel-pipes": "asdf", "read-fd": int, "write-fd": int}
    #     - read/write pipes
    #     - closing pipes closes subchannel?

    ## so, simulate "this" thing wanting to open a subchannel

    # 1. create a pair of pipes
    ri, wi = os.pipe()
    ro, wo = os.pipe()

    # we keep ri, wo and send over ro, wi
    pipe_id = "asdf"  # random ID, must be unique
    data = json.dumps({
        "subchannel-pipes": pipe_id,
        "read-fd": ro,
        "write-fd": wi,
    }).encode("utf8")
    # send sockets to the parent / "wormhole connect"
    sendmsg(child_sock, data, [
        (SOL_SOCKET, SCM_RIGHTS, struct.pack("i", ro)),
        (SOL_SOCKET, SCM_RIGHTS, struct.pack("i", wi)),
    ])
    # tell the "wormhole connect" side to open a subchannel
    # (XXX make switch these around, or just do the whole comms on the unix-socket?)
    print(json.dumps({
        "kind": "open-subchannel",
        "id": pipe_id,
    }))

    # so (wo, ri) are our ends of the pipes

    # write some data down the pipe
    for _ in range(5):
        os.write(wo, b"hello world\n")
        time.sleep(1)
    # (if we expected anything back, we'd read on ri)
    data = os.read(ri, 1024)
    print(json.dumps({
        "from-parent": data.decode("utf8"),
    }))
    os.close(wo)
    os.close(ri)


async def main(reactor):
    """
    this acts like 'wormhole connect'

    so, it starts child() in a subprocess, passing it the paired control socket
    """

    class ChildComms(ProcessProtocol):
        done = Deferred()

        def connectionMade(self):
            print("child up")

        def outReceived(self, data):
            print(f"out: {data}")
            try:
                msg = json.loads(data)
            except Exception as e:
                print(f"decode error: {data}: {e}")
                return
            print(f"msg: {msg}")

        def errReceived(self, data):
            print(f"ERR: {data}")

        def processExited(self, reason):
            if not isinstance(reason.value, ProcessDone):
                print(f"end: {reason}")
            self.done.callback(None)

    child_sock, parent_sock = socketpair()
    setNonBlocking(parent_sock.fileno())

    @implementer(IFileDescriptorReceiver)
    class DescriptorReceiver(Protocol):
        _descriptors = None
        _reader = None
        _writer = None

        def connectionMade(self):
            self._descriptors = []

        def dataReceived(self, data):
            from twisted.internet.process import ProcessReader, ProcessWriter
            read_fd = self._descriptors.pop(0)
            write_fd = self._descriptors.pop(0)
            self._reader = ProcessReader(reactor, self, "read", read_fd)
            self._writer = ProcessWriter(reactor, self, "write", write_fd)
            reactor.addReader(self._reader)
            reactor.addWriter(self._writer)
            print("data", data, self._reader, self._writer)
            self._writer.write(b"ohai\n")

        def fileDescriptorReceived(self, fd):
            print(f"got fd: {fd}")
            self._descriptors.append(fd)

        def childConnectionLost(self, name, reason):
            print("lost", name, reason)

        def childDataReceived(self, name, data):
            print(f"{name}: {data}")

    conn = reactor.adoptStreamConnection(parent_sock.fileno(), AF_UNIX, Factory.forProtocol(DescriptorReceiver))

    print(child_sock, conn)
    proto = ChildComms()
    proc = reactor.spawnProcess(
        proto,
        sys.executable,
        [sys.executable, __file__, "child"],
        childFDs={
            0: "w",
            1: "r",
            2: "r",
            3: child_sock.fileno(),
        },
        env={
            "PYTHONUNBUFFERED": "1",
        }
    )
    print("proc", proc)
    await proto.done


if __name__ == "__main__":
    if "child" in sys.argv:
        # synchronous child
        child()
    else:
        react(main)
