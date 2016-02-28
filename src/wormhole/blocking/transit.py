from __future__ import print_function
import time, threading, socket
from six.moves import socketserver
from binascii import hexlify, unhexlify
from nacl.secret import SecretBox
from ..util import ipaddrs
from ..util.hkdf import HKDF
from ..errors import UsageError
from ..transit_common import (TransitError, BadHandshake, TransitClosed,
                              BadNonce,
                              build_receiver_handshake,
                              build_sender_handshake,
                              build_relay_handshake,
                              parse_hint_tcp)

TIMEOUT=15

# 1: sender only transmits, receiver only accepts, both wait forever
# 2: sender also accepts, receiver also transmits
# 3: timeouts / stop when no more progress can be made
# 4: add relay
# 5: accelerate shutdown of losing sockets

def send_to(skt, data):
    sent = 0
    while sent < len(data):
        sent += skt.send(data[sent:])

def wait_for_line(skt, max_length, description):
    got = b""
    while len(got) < max_length:
        got += skt.recv(1)
        if got.endswith(b"\n"):
            return got[:-1]
    raise BadHandshake("exceeded max_length, got %r on %s" %
                       (got, description))

def wait_for(skt, expected, description):
    assert isinstance(expected, type(b""))
    got = b""
    while len(got) < len(expected):
        got += skt.recv(1)
        if expected[:len(got)] != got:
            raise BadHandshake("got %r want %r on %s" %
                               (got, expected, description))

def debug(msg):
    if False:
        print(msg)
def since(start):
    return time.time() - start

def connector(owner, hint, description,
              send_handshake, expected_handshake, relay_handshake=None):
    start = time.time()
    parsed_hint = parse_hint_tcp(hint)
    if not parsed_hint:
        return # unparseable
    addr,port = parsed_hint
    skt = None
    debug("+ connector(%s)" % hint)
    try:
        skt = socket.create_connection((addr,port),
                                       TIMEOUT) # timeout or ECONNREFUSED
        skt.settimeout(TIMEOUT)
        debug(" - socket(%s) connected CT+%.1f" % (description, since(start)))
        if relay_handshake:
            debug(" - sending relay_handshake")
            send_to(skt, relay_handshake)
            relay_msg = wait_for_line(skt, 10000, description)
            if relay_msg != b"ok":
                raise BadHandshake(relay_msg)
            debug(" - relay ready CT+%.1f" % (since(start),))
        send_to(skt, send_handshake)
        wait_for(skt, expected_handshake, description)
        debug(" + connector(%s) ready CT+%.1f" % (hint, since(start)))
    except Exception as e:
        debug(" - error(%s)(%r) CT+%.1f" % (hint, e, since(start)))
        try:
            if skt:
                skt.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        if skt:
            skt.close()
        # ignore socket errors, warn about coding errors
        if not isinstance(e, (socket.error, socket.timeout, BadHandshake)):
            raise
        debug(" - notifying owner._connector_failed(%s) CT+%.1f" % (hint, since(start)))
        owner._connector_failed(hint)
        return
    # owner is now responsible for the socket
    owner._negotiation_finished(skt, description) # note thread

def handle(skt, client_address, owner, description,
           send_handshake, expected_handshake):
    try:
        debug("handle %r" %  (skt,))
        skt.settimeout(TIMEOUT)
        send_to(skt, send_handshake)
        got = b""
        # for the receiver, this includes the "go\n"
        while len(got) < len(expected_handshake):
            more = skt.recv(1)
            if not more:
                raise BadHandshake("disconnect after merely '%r'" % got)
            got += more
            if expected_handshake[:len(got)] != got:
                raise BadHandshake("got '%r' want '%r'" %
                                   (got, expected_handshake))
        debug("handler negotiation finished %r" % (client_address,))
    except Exception as e:
        debug("handler failed %r" % (client_address,))
        try:
            # this raises socket.err(EBADF) if the socket was already closed
            skt.shutdown(socket.SHUT_WR)
        except socket.error:
            pass
        skt.close() # this appears to be idempotent
        # ignore socket errors, warn about coding errors
        if not isinstance(e, (socket.error, socket.timeout, BadHandshake)):
            raise
        return
    # owner is now responsible for the socket
    owner._negotiation_finished(skt, description) # note thread

class MyTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

    def process_request(self, request, client_address):
        description = "<-tcp:%s:%d" % (client_address[0], client_address[1])
        ready_lock = self.owner._ready_for_connections_lock
        ready_lock.acquire()
        while not (self.owner._ready_for_connections
                   and self.owner._transit_key):
            ready_lock.wait()
        # owner._transit_key is either None or set to a value. We don't
        # modify it from here, so we can release the condition lock before
        # grabbing the key.
        ready_lock.release()

        # Once it is set, we can get handler_(send|receive)_handshake, which
        # is what we actually care about.
        t = threading.Thread(target=handle,
                             args=(request, client_address,
                                   self.owner, description,
                                   self.owner.handler_send_handshake,
                                   self.owner.handler_expected_handshake))
        t.daemon = True
        t.start()


class ReceiveBuffer:
    def __init__(self, skt):
        self.skt = skt
        self.buf = b""

    def read(self, count):
        while len(self.buf) < count:
            more = self.skt.recv(4096)
            if not more:
                raise TransitClosed
            self.buf += more
        rc = self.buf[:count]
        self.buf = self.buf[count:]
        return rc

class RecordPipe:
    def __init__(self, skt, send_key, receive_key, description):
        self.skt = skt
        self.send_box = SecretBox(send_key)
        self.send_nonce = 0
        self.receive_buf = ReceiveBuffer(self.skt)
        self.receive_box = SecretBox(receive_key)
        self.next_receive_nonce = 0
        self._description = description

    def describe(self):
        return self._description

    def send_record(self, record):
        if not isinstance(record, type(b"")): raise UsageError
        assert SecretBox.NONCE_SIZE == 24
        assert self.send_nonce < 2**(8*24)
        assert len(record) < 2**(8*4)
        nonce = unhexlify("%048x" % self.send_nonce) # big-endian
        self.send_nonce += 1
        encrypted = self.send_box.encrypt(record, nonce)
        length = unhexlify("%08x" % len(encrypted)) # always 4 bytes long
        send_to(self.skt, length)
        send_to(self.skt, encrypted)

    def receive_record(self):
        length_buf = self.receive_buf.read(4)
        length = int(hexlify(length_buf), 16)
        encrypted = self.receive_buf.read(length)
        nonce_buf = encrypted[:SecretBox.NONCE_SIZE] # assume it's prepended
        nonce = int(hexlify(nonce_buf), 16)
        if nonce != self.next_receive_nonce:
            raise BadNonce("received out-of-order record")
        self.next_receive_nonce += 1
        record = self.receive_box.decrypt(encrypted)
        return record

    def close(self):
        self.skt.close()

class Common:
    def __init__(self, transit_relay):
        if transit_relay:
            if not isinstance(transit_relay, type(u"")):
                raise UsageError
            self._transit_relays = [transit_relay]
        else:
            self._transit_relays = []
        self.winning = threading.Event()
        self._negotiation_check_lock = threading.Lock()
        self._ready_for_connections_lock = threading.Condition()
        self._ready_for_connections = False
        self._transit_key = None
        self._start_server()

    def _start_server(self):
        server = MyTCPServer(("", 0), None)
        _, port = server.server_address
        self.my_direct_hints = [u"tcp:%s:%d" % (addr, port)
                                for addr in ipaddrs.find_addresses()]
        server.owner = self
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        self.listener = server

    def get_direct_hints(self):
        return self.my_direct_hints
    def get_relay_hints(self):
        return self._transit_relays

    def add_their_direct_hints(self, hints):
        for h in hints:
            if not isinstance(h, type(u"")):
                raise TypeError("hint '%r' should be unicode, not %s"
                                % (h, type(h)))
        self._their_direct_hints = list(hints)
    def add_their_relay_hints(self, hints):
        for h in hints:
            if not isinstance(h, type(u"")):
                raise TypeError("hint '%r' should be unicode, not %s"
                                % (h, type(h)))
        self._their_relay_hints = list(hints)

    def _send_this(self):
        if self.is_sender:
            return build_sender_handshake(self._transit_key)
        else:
            return build_receiver_handshake(self._transit_key)

    def _expect_this(self):
        if self.is_sender:
            return build_receiver_handshake(self._transit_key)
        else:
            return build_sender_handshake(self._transit_key) + b"go\n"

    def _sender_record_key(self):
        if self.is_sender:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_sender_key")
        else:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_receiver_key")

    def _receiver_record_key(self):
        if self.is_sender:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_receiver_key")
        else:
            return HKDF(self._transit_key, SecretBox.KEY_SIZE,
                        CTXinfo=b"transit_record_sender_key")

    def set_transit_key(self, key):
        # This _ready_for_connections condition/lock protects us against the
        # race where the sender knows the hints and the key, and connects to
        # the receiver's transit socket before the receiver gets relay
        # message (and thus the key).
        self._ready_for_connections_lock.acquire()
        self._transit_key = key
        self.handler_send_handshake = self._send_this() # no "go"
        self.handler_expected_handshake = self._expect_this()
        self._ready_for_connections_lock.notify_all()
        self._ready_for_connections_lock.release()

    def _start_outbound(self):
        self._active_connectors = set(self._their_direct_hints)
        self._attempted_connectors = set()
        for hint in self._their_direct_hints:
            self._start_connector(hint)
        if not self._their_direct_hints:
            self._start_relay_connectors()

    def _start_connector(self, hint, is_relay=False):
        # Don't try any hint more than once. If all hints fail, we'll
        # eventually timeout. We make no attempt to fail any faster.
        if hint in self._attempted_connectors:
            return
        self._attempted_connectors.add(hint)
        description = "->%s" % (hint,)
        if is_relay:
            description = "->relay:%s" % (hint,)
        args = (self, hint, description,
                self._send_this(), self._expect_this())
        if is_relay:
            args = args + (build_relay_handshake(self._transit_key),)
        t = threading.Thread(target=connector, args=args)
        t.daemon = True
        t.start()

    def _start_relay_connectors(self):
        self._active_connectors.update(self._their_direct_hints)
        for hint in self._their_relay_hints:
            self._start_connector(hint, is_relay=True)

    def establish_socket(self):
        start = time.time()
        self.winning_skt = None
        self.winning_skt_description = None
        self._ready_for_connections_lock.acquire()
        self._ready_for_connections = True
        self._ready_for_connections_lock.notify_all()
        self._ready_for_connections_lock.release()
        self._start_outbound()

        # we sit here until one of our inbound or outbound sockets succeeds
        flag = self.winning.wait(2*TIMEOUT)
        debug("wait returned at %.1f" % (since(start),))

        if not flag:
            # timeout: self.winning_skt will not be set. ish. race.
            pass
        if self.listener:
            self.listener.shutdown() # TODO: waits up to 0.5s. push to thread
        if self.winning_skt:
            return self.winning_skt
        raise TransitError("timeout")

    def _connector_failed(self, hint):
        debug("- failed connector %s" % hint)
        # XXX this was .remove, and occasionally got KeyError
        self._active_connectors.discard(hint)
        if not self._active_connectors:
            self._start_relay_connectors()

    def _negotiation_finished(self, skt, description):
        # inbound/outbound sockets call this when they finish negotiation.
        # The first one wins and gets a "go". Any subsequent ones lose and
        # get a "nevermind" before being closed.

        with self._negotiation_check_lock:
            if self.winning_skt:
                is_winner = False
            else:
                is_winner = True
                self.winning_skt = skt
                self.winning_skt_description = description

        if is_winner:
            if self.is_sender:
                send_to(skt, b"go\n")
            self.winning.set()
        else:
            if self.is_sender:
                send_to(skt, b"nevermind\n")
            skt.close()

    def connect(self):
        skt = self.establish_socket()
        return RecordPipe(skt, self._sender_record_key(),
                          self._receiver_record_key(),
                          self.winning_skt_description)

class TransitSender(Common):
    is_sender = True

class TransitReceiver(Common):
    is_sender = False
