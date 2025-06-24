import wormhole
from zope.interface import implementer, alsoProvides
from twisted.internet.defer import Deferred, gatherResults
from twisted.internet.protocol import Protocol, Factory
from twisted.internet.interfaces import IProtocolFactory

import pytest
import pytest_twisted

from ..common import poll_until
from ..._interfaces import IDilationConnector
from ...eventual import EventualQueue
from ..._dilation._noise import NoiseConnection


APPID = "lothar.com/dilate-test"


def doBoth(d1, d2):
    return gatherResults([d1, d2], True)


class HelloProtocol(Protocol):
    def connectionMade(self):
        print("got connection")
        self.transport.write(b"hello\n")

    def dataReceived(self, data):
        print(f"dataReceived: {data}")
        self.factory.d.callback(data)

    def connectionLost(self, why):
        print("connectionLost")


class BonjourProtocol(Protocol):
    """
    A french version of HelloProtocol
    """
    def connectionMade(self):
        self.transport.write(b"bonjour\n")

    def dataReceived(self, data):
        self.factory.d.callback(data)

    def connectionLost(self, why):
        pass


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_single_subprotocol(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w2 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w1.allocate_code()
    code = await w1.get_code()
    print(f"code is: {code}")
    w2.set_code(code)
    await doBoth(w1.get_verifier(), w2.get_verifier())
    print("connected")

    # side "0" is the host / listener, side "1" is the guest / connector
    fserv0 = Factory()
    fserv0.d = Deferred()
    fserv0.protocol = HelloProtocol

    eps1 = w1.dilate()
    eps2 = w2.dilate()
    print("w.dilate ready")

    eps1.listener_for("proto").listen(fserv0)

    f2 = Factory()
    alsoProvides(f2, IProtocolFactory)
    f2.protocol = HelloProtocol
    f2.d = Deferred()
    f2.d.addCallback(lambda data: eq.fire_eventually(data))
    d2 = eps2.connector_for("proto").connect(f2)
    await d2

    # note: I'm making some horrible assumptions about one-to-one writes
    # and reads across a TCP stack that isn't obligated to maintain such
    # a relationship, but it's much easier than doing this properly. If
    # the tests ever start failing, do the extra work, probably by
    # using a twisted.protocols.basic.LineOnlyReceiver
    data1 = await fserv0.d
    data2 = await f2.d
    assert data1 == b"hello\n"
    assert data2 == b"hello\n"

    await w2.close()


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_double_subprotocol(reactor, mailbox):
    """
    Two subprotocols with different names / implementations on the
    same wormhole.
    """
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w2 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w1.allocate_code()
    code = await w1.get_code()
    print("code is: {}".format(code))
    w2.set_code(code)
    await doBoth(w1.get_verifier(), w2.get_verifier())
    print("connected")

    # side "0" is the host / listener, side "1" is the guest / connector
    fserv0 = Factory()
    fserv0.d = Deferred()
    fserv0.protocol = HelloProtocol

    fserv1 = Factory()
    fserv1.d = Deferred()
    fserv1.protocol = BonjourProtocol

    host = w1.dilate()
    guest = w2.dilate()

    host.listener_for("hello").listen(fserv0)
    host.listener_for("bonjour").listen(fserv1)

    f2 = Factory.forProtocol(HelloProtocol)
    f2.d = Deferred()
    f2.d.addCallback(lambda data: eq.fire_eventually(data))

    f3 = Factory.forProtocol(BonjourProtocol)
    f3.d = Deferred()
    f3.d.addCallback(lambda data: eq.fire_eventually(data))

    d2 = guest.connector_for("hello").connect(f2)
    d3 = guest.connector_for("bonjour").connect(f3)
    await d2
    await d3

    data1 = await fserv0.d
    data2 = await f2.d
    assert data1 == b"hello\n"
    assert data2 == b"hello\n"

    data3 = await fserv1.d
    data4 = await f3.d
    assert data3 == data4 == b"bonjour\n"

    await w2.close()


class ReconP(Protocol):
    def eventually(self, which, data):
        d = self.factory.deferreds[which]
        self.factory.eq.fire_eventually(data).addCallback(d.callback)

    def connectionMade(self):
        self.eventually("connectionMade", self)
        # self.transport.write(b"hello\n")

    def dataReceived(self, data):
        self.eventually("dataReceived", data)

    def connectionLost(self, why):
        self.eventually("connectionLost", (self, why))


@implementer(IProtocolFactory)
class ReconF(Factory):
    protocol = ReconP
    subprotocol = "proto"

    def __init__(self, eq):
        Factory.__init__(self)
        self.eq = eq
        self.deferreds = {}
        for name in ["connectionMade", "dataReceived", "connectionLost"]:
            self.deferreds[name] = Deferred()

    def resetDeferred(self, name):
        d = Deferred()
        self.deferreds[name] = d
        return d


@implementer(IProtocolFactory)
class SubFac(Factory):
    pass


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_reconnect(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w2 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    await doBoth(w1.get_verifier(), w2.get_verifier())

    f1 = ReconF(eq)

    eps1 = w1.dilate()
    eps2 = w2.dilate()
    print("w.dilate ready")

    eps1.listener_for("proto").listen(f1)

    f2 = ReconF(eq)
    d2 = eps2.connector_for("proto").connect(f2)
    await d2

    protocols = {}

    def p_connected(p, index):
        protocols[index] = p
        msg = f"hello from {index}\n"
        p.transport.write(msg.encode("ascii"))
    f1.deferreds["connectionMade"].addCallback(p_connected, 1)
    f2.deferreds["connectionMade"].addCallback(p_connected, 2)

    data1 = await f1.deferreds["dataReceived"]
    data2 = await f2.deferreds["dataReceived"]
    assert data1 == b"hello from 2\n"
    assert data2 == b"hello from 1\n"
    # the ACKs are now in flight and may not arrive before we kill the
    # connection

    f1.resetDeferred("connectionMade")
    f2.resetDeferred("connectionMade")
    f1.resetDeferred("dataReceived")
    d2 = f2.resetDeferred("dataReceived")

    # now we reach inside and drop the connection
    sc = protocols[1].transport
    orig_connection = sc._manager._connection
    orig_connection.disconnect()

    # stall until the connection has been replaced
    await poll_until(lambda: sc._manager._connection
                     and (orig_connection != sc._manager._connection))

    # now write some more data, which should travel over the new
    # connection
    protocols[1].transport.write(b"more\n")
    data2 = await d2
    assert data2 == b"more\n"

    replacement_connection = sc._manager._connection
    assert orig_connection != replacement_connection

    # the application-visible Protocol should not observe the
    # interruption
    assert not f1.deferreds["connectionMade"].called
    assert not f2.deferreds["connectionMade"].called
    assert not f1.deferreds["connectionLost"].called
    assert not f2.deferreds["connectionLost"].called

    await w1.close()
    await w2.close()

@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_data_while_offline(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w2 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    await doBoth(w1.get_verifier(), w2.get_verifier())

    f1 = ReconF(eq)
    eps1 = w1.dilate()
    eps2 = w2.dilate()
    print("w.dilate ready")

    eps1.listener_for("proto").listen(f1)

    f2 = ReconF(eq)
    d2 = eps2.connector_for("proto").connect(f2)
    await d2

    protocols = {}

    def p_connected(p, index):
        protocols[index] = p
        msg = f"hello from {index}\n"
        p.transport.write(msg.encode("ascii"))
    f1.deferreds["connectionMade"].addCallback(p_connected, 1)
    f2.deferreds["connectionMade"].addCallback(p_connected, 2)

    data1 = await f1.deferreds["dataReceived"]
    data2 = await f2.deferreds["dataReceived"]
    assert data1 == b"hello from 2\n"
    assert data2 == b"hello from 1\n"
    # the ACKs are now in flight and may not arrive before we kill the
    # connection

    f1.resetDeferred("connectionMade")
    f2.resetDeferred("connectionMade")
    d1 = f1.resetDeferred("dataReceived")
    d2 = f2.resetDeferred("dataReceived")

    # switch off connections
    assert not w1._boss._D._manager._debug_stall_connector
    cd1, cd2 = Deferred(), Deferred()
    w1._boss._D._manager._debug_stall_connector = cd1.callback
    w2._boss._D._manager._debug_stall_connector = cd2.callback

    # now we reach inside and drop the connection
    sc = protocols[1].transport
    orig_connection = sc._manager._connection
    orig_connection.disconnect()

    c1 = await cd1
    c2 = await cd2
    assert IDilationConnector.providedBy(c1)
    assert IDilationConnector.providedBy(c2)
    assert c1 is not orig_connection
    w1._boss._D._manager._debug_stall_connector = False
    w2._boss._D._manager._debug_stall_connector = False

    # now write some data while the connection is definitely offline
    protocols[1].transport.write(b"more 1->2\n")
    protocols[2].transport.write(b"more 2->1\n")

    # allow the connections to proceed
    c1.start()
    c2.start()

    # and wait for the data to arrive
    data2 = await d2
    assert data2 == b"more 1->2\n"
    data1 = await d1
    assert data1 == b"more 2->1\n"

    # the application-visible Protocol should not observe the
    # interruption
    assert not f1.deferreds["connectionMade"].called
    assert not f2.deferreds["connectionMade"].called
    assert not f1.deferreds["connectionLost"].called
    assert not f2.deferreds["connectionLost"].called

    await w1.close()
    await w2.close()


@pytest_twisted.ensureDeferred()
@pytest.mark.skipif(not NoiseConnection, reason="noiseprotocol required")
async def test_endpoints(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w2 = wormhole.create(APPID, mailbox.url, reactor, dilation=True)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    await doBoth(w1.get_verifier(), w2.get_verifier())

    f0 = ReconF(eq)
    eps1 = w1.dilate()
    eps2 = w2.dilate()
    print("w.dilate ready")
    eps1.listener_for("proto").listen(f0)

    from twisted.python import log
    f1 = ReconF(eq)
    log.msg("connecting")
    p1_client = await eps2.connector_for("proto").connect(f1)
    log.msg("sending c->s")
    p1_client.transport.write(b"hello from p1\n")
    data = await f0.deferreds["dataReceived"]
    assert data == b"hello from p1\n"
    p1_server = await f0.deferreds["connectionMade"]
    log.msg("sending s->c")
    p1_server.transport.write(b"hello p1\n")
    log.msg("waiting for client to receive")
    data = await f1.deferreds["dataReceived"]
    assert data == b"hello p1\n"

    # open a second channel
    f0.resetDeferred("connectionMade")
    f0.resetDeferred("dataReceived")
    f1.resetDeferred("dataReceived")
    f2 = ReconF(eq)
    p2_client = await eps2.connector_for("proto").connect(f2)
    p2_server = await f0.deferreds["connectionMade"]
    p2_server.transport.write(b"hello p2\n")
    data = await f2.deferreds["dataReceived"]
    assert data == b"hello p2\n"
    p2_client.transport.write(b"hello from p2\n")
    data = await f0.deferreds["dataReceived"]
    assert data == b"hello from p2\n"
    assert not f1.deferreds["dataReceived"].called

    # now close the first subchannel (p1) from the listener side
    p1_server.transport.loseConnection()
    await f0.deferreds["connectionLost"]
    await f1.deferreds["connectionLost"]

    f0.resetDeferred("connectionLost")
    # and close the second from the connector side
    p2_client.transport.loseConnection()
    await f0.deferreds["connectionLost"]
    await f2.deferreds["connectionLost"]

    await w1.close()
    await w2.close()
