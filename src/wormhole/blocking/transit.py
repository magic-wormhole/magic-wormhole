import os, threading, socket, SocketServer
from binascii import hexlify
from ..util import ipaddrs
from ..util.hkdf import HKDF

class ThreadedTCPServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    pass

def build_receiver_handshake(key):
    return "rx\n\n"
    hexid = HKDF(key, 32, CTXinfo=b"transit_receiver")
    return "transit receiver %s ready\n\n" % hexlify(hexid)

def build_sender_handshake(key):
    return "tx\n\n"
    hexid = HKDF(key, 32, CTXinfo=b"transit_sender")
    return "transit sender %s ready\n\n" % hexlify(hexid)

class TransitSender:
    def __init__(self):
        self.key = os.urandom(32)
    def get_transit_key(self):
        return self.key
    def get_direct_hints(self):
        return []
    def get_relay_hints(self):
        return []
    def add_receiver_hints(self, hints):
        self.receiver_hints = hints
    def establish_connection(self):
        sender_handshake = build_sender_handshake(self.key)
        receiver_handshake = build_receiver_handshake(self.key)
        self.connectors = []
        for hint in self.receiver_hints:
            connector = _Connector(hint, sender_handshake, receiver_handshake)
            connector.start()
            self.connectors.append(connector)
    def write(self, data):
        pass
    def close(self):
        pass

class BadHandshake(Exception):
    pass

class _Connector(threading.Thread):
    def __init__(self, owner, hint, send_handshake, expected_handshake):
        threading.Thread.__init__(self)
        self.owner = owner
        self.hint = hint
        self.send_handshake = send_handshake
        self.expected_handshake = expected_handshake

    def run(self):
        addr,port = self.hint.split(",")
        skt = socket.create_connection((addr,port))
        print "socket(%s) connected" % self.hint
        skt.send(self.send_handshake)
        got = b""
        while len(got) < len(self.expected_handshake):
            got += skt.recv(1)
            if self.expected_handshake[:len(got)] != got:
                raise BadHandshake("got '%r' want '%r' on %s" %
                                   (got, self.expected_handshake, self.hint))
        print "connector ready", self.hint
        self.owner.connector_connected(skt) # note thread
        skt.close()



class MyTCPServer(SocketServer.TCPServer):
    allow_reuse_address = True
    def process_request(self, request, client_address):
        # if the handler returns True, it has given the socket to someone
        # else, and we should not close it
        handler = _Handler()
        t = threading.Thread(target=handler.handle,
                             args=(SERVER, request, client_address, X))
        t.daemon = False
        t.start()
        self.threads.append(t)

        try:
            
            claimed = self.finish_request(request, client_address)
            if not claimed:
                self.shutdown_request(request)
        except:
            self.handle_error(request, client_address)
            self.shutdown_request(request)

class _Handler:
    def handle(self, tr, skt, client_address, X):
        try:
            print "handle", skt
            skt.settimeout(5.0)
            send_handshake = tr.handler_send_handshake
            expected_handshake = tr.handler_expected_handshake
            skt.send(send_handshake)
            got = b""
            while len(got) < len(expected_handshake):
                got += skt.recv(1)
                if expected_handshake[:len(got)] != got:
                    raise BadHandshake("got '%r' want '%r'" %
                                       (got, expected_handshake))
            print "handler ready", server_address, client_address
            # give skt to somebody
            tr.handler_connected(skt) # note thread
        except:
            try:
                skt.shutdown(socket.SHUT_WR)
            except socket.error:
                pass
            skt.close()
            server.handler_closed()

class TransitReceiver:
    def __init__(self):
        self.addrs = ipaddrs.find_addresses()
        self.my_direct_hints = []
        self.my_listeners = []
        for addr in self.addrs:
            server = MyThreadingTCPServer((addr,9999), _Handler)
            server.receiver = self
            ip, port = server.server_address
            server_thread = threading.Thread(target=server.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            self.my_direct_hints.append("%s,%d" % (addr, port))
            self.my_listeners.append(server)

    def shutdown(self):
        for server in self.my_listeners:
            server.shutdown()

    def get_direct_hints(self):
        return self.my_direct_hints
    def set_transit_key(self, key):
        self.key = key
        self.handler_send_handshake = build_receiver_handshake(key)
        self.handler_expected_handshake = build_sender_handshake(key)

    def add_sender_direct_hints(self, hints):
        self.sender_direct_hints = hints # TODO ignored
    def add_sender_relay_hints(self, hints):
        self.sender_relay_hints = hints # TODO ignored

    def connection_resolved(self, x):
        # get lock
        # update pending connection list
        # determine next step
        # release lock
        # take next step
        if good_socket:
            # cancel listener(s)
            # cancel handlers (still in negotiation)
            # cancel connectors (waiting connection or in negotiation)
            
    def establish_connection(self):
        assert self.key
        # start stuff
        # wait for a connection to be made
        self.ready = threading.Event()
        self.ready.wait()
        print "connection established"
        print self.skt
        return self.skt

    def handler_connected(self, skt):
        self.skt = skt
        self.ready.set()

    def receive(self):
        pass
