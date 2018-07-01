from attr import attrs, attrib
from attr.validators import instance_of, provides
from zope.interface import implementer
from twisted.internet.defer import Deferred, inlineCallbacks, returnValue, succeed
from twisted.internet.interfaces import (ITransport, IProducer, IConsumer,
                                         IAddress, IListeningPort,
                                         IStreamClientEndpoint,
                                         IStreamServerEndpoint)
from twisted.internet.error import ConnectionDone
from automat import MethodicalMachine
from .._interfaces import ISubChannel, IDilationManager

@attrs
class Once(object):
    _errtype = attrib()
    def __attrs_post_init__(self):
        self._called = False

    def __call__(self):
        if self._called:
            raise self._errtype()
        self._called = True

class SingleUseEndpointError(Exception):
    pass

# created in the (OPEN) state, by either:
#  * receipt of an OPEN message
#  * or local client_endpoint.connect()
# then transitions are:
# (OPEN) rx DATA: deliver .dataReceived(), -> (OPEN)
# (OPEN) rx CLOSE: deliver .connectionLost(), send CLOSE, -> (CLOSED)
# (OPEN) local .write(): send DATA, -> (OPEN)
# (OPEN) local .loseConnection(): send CLOSE, -> (CLOSING)
# (CLOSING) local .write(): error
# (CLOSING) local .loseConnection(): error
# (CLOSING) rx DATA: deliver .dataReceived(), -> (CLOSING)
# (CLOSING) rx CLOSE: deliver .connectionLost(), -> (CLOSED)
# object is deleted upon transition to (CLOSED)

class AlreadyClosedError(Exception):
    pass

@implementer(IAddress)
class _WormholeAddress(object):
    pass

@implementer(IAddress)
@attrs
class _SubchannelAddress(object):
    _scid = attrib()


@attrs
@implementer(ITransport)
@implementer(IProducer)
@implementer(IConsumer)
@implementer(ISubChannel)
class SubChannel(object):
    _id = attrib(validator=instance_of(bytes))
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=instance_of(_WormholeAddress))
    _peer_addr = attrib(validator=instance_of(_SubchannelAddress))

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self, f: None) # pragma: no cover

    def __attrs_post_init__(self):
        #self._mailbox = None
        #self._pending_outbound = {}
        #self._processed = set()
        self._protocol = None
        self._pending_dataReceived = []
        self._pending_connectionLost = (False, None)

    @m.state(initial=True)
    def open(self): pass # pragma: no cover

    @m.state()
    def closing(): pass # pragma: no cover

    @m.state()
    def closed(): pass # pragma: no cover

    @m.input()
    def remote_data(self, data): pass
    @m.input()
    def remote_close(self): pass

    @m.input()
    def local_data(self, data): pass
    @m.input()
    def local_close(self): pass


    @m.output()
    def send_data(self, data):
        self._manager.send_data(self._id, data)

    @m.output()
    def send_close(self):
        self._manager.send_close(self._id)

    @m.output()
    def signal_dataReceived(self, data):
        if self._protocol:
            self._protocol.dataReceived(data)
        else:
            self._pending_dataReceived.append(data)

    @m.output()
    def signal_connectionLost(self):
        if self._protocol:
            self._protocol.connectionLost(ConnectionDone())
        else:
            self._pending_connectionLost = (True, ConnectionDone())
        self._manager.subchannel_closed(self)
        # we're deleted momentarily

    @m.output()
    def error_closed_write(self, data):
        raise AlreadyClosedError("write not allowed on closed subchannel")
    @m.output()
    def error_closed_close(self):
        raise AlreadyClosedError("loseConnection not allowed on closed subchannel")

    # primary transitions
    open.upon(remote_data, enter=open, outputs=[signal_dataReceived])
    open.upon(local_data, enter=open, outputs=[send_data])
    open.upon(remote_close, enter=closed, outputs=[signal_connectionLost])
    open.upon(local_close, enter=closing, outputs=[send_close])
    closing.upon(remote_data, enter=closing, outputs=[signal_dataReceived])
    closing.upon(remote_close, enter=closed, outputs=[signal_connectionLost])

    # error cases
    # we won't ever see an OPEN, since L4 will log+ignore those for us
    closing.upon(local_data, enter=closing, outputs=[error_closed_write])
    closing.upon(local_close, enter=closing, outputs=[error_closed_close])
    # the CLOSED state won't ever see messages, since we'll be deleted

    # our endpoints use this

    def _set_protocol(self, protocol):
        assert not self._protocol
        self._protocol = protocol
        if self._pending_dataReceived:
            for data in self._pending_dataReceived:
                self._protocol.dataReceived(data)
            self._pending_dataReceived =  []
        cl, what = self._pending_connectionLost
        if cl:
            self._protocol.connectionLost(what)

    # ITransport
    def write(self, data):
        assert isinstance(data, type(b""))
        self.local_data(data)
    def writeSequence(self, iovec):
        self.write(b"".join(iovec))
    def loseConnection(self):
        self.local_close()
    def getHost(self):
        # we define "host addr" as the overall wormhole
        return self._host_addr
    def getPeer(self):
        # and "peer addr" as the subchannel within that wormhole
        return self._peer_addr

    # IProducer: throttle inbound data (wormhole "up" to local app's Protocol)
    def stopProducing(self):
        self._manager.subchannel_stopProducing(self)
    def pauseProducing(self):
        self._manager.subchannel_pauseProducing(self)
    def resumeProducing(self):
        self._manager.subchannel_resumeProducing(self)

    # IConsumer: allow the wormhole to throttle outbound data (app->wormhole)
    def registerProducer(self, producer, streaming):
        self._manager.subchannel_registerProducer(self, producer, streaming)
    def unregisterProducer(self):
        self._manager.subchannel_unregisterProducer(self)


@implementer(IStreamClientEndpoint)
class ControlEndpoint(object):
    _used = False
    def __init__(self, peer_addr):
        self._subchannel_zero = Deferred()
        self._peer_addr = peer_addr
        self._once = Once(SingleUseEndpointError)

    # from manager
    def _subchannel_zero_opened(self, subchannel):
        assert ISubChannel.providedBy(subchannel), subchannel
        self._subchannel_zero.callback(subchannel)

    @inlineCallbacks
    def connect(self, protocolFactory):
        # return Deferred that fires with IProtocol or Failure(ConnectError)
        self._once()
        t = yield self._subchannel_zero
        p = protocolFactory.buildProtocol(self._peer_addr)
        t._set_protocol(p)
        p.makeConnection(t) # set p.transport = t and call connectionMade()
        returnValue(p)

@implementer(IStreamClientEndpoint)
@attrs
class SubchannelConnectorEndpoint(object):
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=instance_of(_WormholeAddress))

    def connect(self, protocolFactory):
        # return Deferred that fires with IProtocol or Failure(ConnectError)
        scid = self._manager.allocate_subchannel_id()
        self._manager.send_open(scid)
        peer_addr = _SubchannelAddress(scid)
        # ? f.doStart()
        # ? f.startedConnecting(CONNECTOR) # ??
        t = SubChannel(scid, self._manager, self._host_addr, peer_addr)
        p = protocolFactory.buildProtocol(peer_addr)
        t._set_protocol(p)
        p.makeConnection(t) # set p.transport = t and call connectionMade()
        return succeed(p)

@implementer(IStreamServerEndpoint)
@attrs
class SubchannelListenerEndpoint(object):
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=provides(IAddress))

    def __attrs_post_init__(self):
        self._factory = None
        self._pending_opens = []

    # from manager
    def _got_open(self, t, peer_addr):
        if self._factory:
            self._connect(t, peer_addr)
        else:
            self._pending_opens.append( (t, peer_addr) )

    def _connect(self, t, peer_addr):
        p = self._factory.buildProtocol(peer_addr)
        t._set_protocol(p)
        p.makeConnection(t)

    # IStreamServerEndpoint

    def listen(self, protocolFactory):
        self._factory = protocolFactory
        for (t, peer_addr) in self._pending_opens:
            self._connect(t, peer_addr)
        self._pending_opens = []
        lp = SubchannelListeningPort(self._host_addr)
        return succeed(lp)

@implementer(IListeningPort)
@attrs
class SubchannelListeningPort(object):
    _host_addr = attrib(validator=provides(IAddress))

    def startListening(self):
        pass
    def stopListening(self):
        # TODO
        pass
    def getHost(self):
        return self._host_addr
