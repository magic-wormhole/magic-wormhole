from collections import deque
from attr import attrs, attrib
from attr.validators import instance_of, provides
from zope.interface import implementer
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.interfaces import (ITransport, IProducer, IConsumer,
                                         IAddress, IListeningPort,
                                         IHalfCloseableProtocol,
                                         IStreamClientEndpoint,
                                         IStreamServerEndpoint)
from twisted.internet.error import ConnectionDone
from automat import MethodicalMachine
from .._interfaces import ISubChannel, IDilationManager
from ..observer import OneShotObserver

# each subchannel frame (the data passed into transport.write(data)) gets a
# 9-byte header prefix (type, subchannel id, and sequence number), then gets
# encrypted (adding a 16-byte authentication tag). The result is transmitted
# with a 4-byte length prefix (which only covers the padded message, not the
# length prefix itself), so the padded message must be less than 2**32 bytes
# long.
MAX_FRAME_LENGTH = 2**32 - 1 - 9 - 16


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


class NormalCloseUsedOnHalfCloseable(Exception):
    pass


class HalfCloseUsedOnNonHalfCloseable(Exception):
    pass


@implementer(IAddress)
class _WormholeAddress(object):
    pass


@implementer(IAddress)
@attrs
class _SubchannelAddress(object):
    _scid = attrib(validator=instance_of(int))


@attrs(eq=False)
@implementer(ITransport)
@implementer(IProducer)
@implementer(IConsumer)
@implementer(ISubChannel)
class SubChannel(object):
    _scid = attrib(validator=instance_of(int))
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=instance_of(_WormholeAddress))
    _peer_addr = attrib(validator=instance_of(_SubchannelAddress))

    m = MethodicalMachine()
    set_trace = getattr(m, "_setTrace", lambda self,
                        f: None)  # pragma: no cover

    def __attrs_post_init__(self):
        # self._mailbox = None
        # self._pending_outbound = {}
        # self._processed = set()
        self._protocol = None
        self._pending_remote_data = []
        self._pending_remote_close = False

    @m.state(initial=True)
    def unconnected(self):
        pass  # pragma: no cover

    # once we get the IProtocol, it's either a IHalfCloseableProtocol, or it
    # can only be fully closed
    @m.state()
    def open_half(self):
        pass  # pragma: no cover

    @m.state()
    def read_closed():
        pass  # pragma: no cover

    @m.state()
    def write_closed():
        pass  # pragma: no cover

    @m.state()
    def open_full(self):
        pass  # pragma: no cover

    @m.state()
    def closing():
        pass  # pragma: no cover

    @m.state()
    def closed():
        pass  # pragma: no cover

    @m.input()
    def connect_protocol_half(self):
        pass

    @m.input()
    def connect_protocol_full(self):
        pass

    @m.input()
    def remote_data(self, data):
        pass

    @m.input()
    def remote_close(self):
        pass

    @m.input()
    def local_data(self, data):
        pass

    @m.input()
    def local_close(self):
        pass

    @m.output()
    def queue_remote_data(self, data):
        self._pending_remote_data.append(data)

    @m.output()
    def queue_remote_close(self):
        self._pending_remote_close = True

    @m.output()
    def send_data(self, data):
        self._manager.send_data(self._scid, data)

    @m.output()
    def send_close(self):
        self._manager.send_close(self._scid)

    @m.output()
    def signal_dataReceived(self, data):
        assert self._protocol
        self._protocol.dataReceived(data)

    @m.output()
    def signal_readConnectionLost(self):
        IHalfCloseableProtocol(self._protocol).readConnectionLost()

    @m.output()
    def signal_writeConnectionLost(self):
        IHalfCloseableProtocol(self._protocol).writeConnectionLost()

    @m.output()
    def signal_connectionLost(self):
        assert self._protocol
        self._protocol.connectionLost(ConnectionDone())

    @m.output()
    def close_subchannel(self):
        self._manager.subchannel_closed(self._scid, self)
        # we're deleted momentarily

    @m.output()
    def error_closed_write(self, data):
        raise AlreadyClosedError("write not allowed on closed subchannel")

    @m.output()
    def error_closed_close(self):
        raise AlreadyClosedError(
            "loseConnection not allowed on closed subchannel")

    # stuff that arrives before we have a protocol connected
    unconnected.upon(remote_data, enter=unconnected, outputs=[queue_remote_data])
    unconnected.upon(remote_close, enter=unconnected, outputs=[queue_remote_close])

    # IHalfCloseableProtocol flow
    unconnected.upon(connect_protocol_half, enter=open_half, outputs=[])
    open_half.upon(remote_data, enter=open_half, outputs=[signal_dataReceived])
    open_half.upon(local_data, enter=open_half, outputs=[send_data])
    # remote closes first
    open_half.upon(remote_close, enter=read_closed, outputs=[signal_readConnectionLost])
    read_closed.upon(local_data, enter=read_closed, outputs=[send_data])
    read_closed.upon(local_close, enter=closed, outputs=[send_close,
                                                         close_subchannel,
                                                         # TODO: eventual-signal this?
                                                         signal_writeConnectionLost,
                                                         ])
    # local closes first
    open_half.upon(local_close, enter=write_closed, outputs=[signal_writeConnectionLost,
                                                             send_close])
    write_closed.upon(local_data, enter=write_closed, outputs=[error_closed_write])
    write_closed.upon(remote_data, enter=write_closed, outputs=[signal_dataReceived])
    write_closed.upon(remote_close, enter=closed, outputs=[close_subchannel,
                                                           signal_readConnectionLost,
                                                           ])
    # error cases
    write_closed.upon(local_close, enter=write_closed, outputs=[error_closed_close])

    # fully-closeable-only flow
    unconnected.upon(connect_protocol_full, enter=open_full, outputs=[])
    open_full.upon(remote_data, enter=open_full, outputs=[signal_dataReceived])
    open_full.upon(local_data, enter=open_full, outputs=[send_data])
    open_full.upon(remote_close, enter=closed, outputs=[send_close,
                                                        close_subchannel,
                                                        signal_connectionLost])
    open_full.upon(local_close, enter=closing, outputs=[send_close])
    closing.upon(remote_data, enter=closing, outputs=[signal_dataReceived])
    closing.upon(remote_close, enter=closed, outputs=[close_subchannel,
                                                      signal_connectionLost])
    # error cases
    # we won't ever see an OPEN, since L4 will log+ignore those for us
    closing.upon(local_data, enter=closing, outputs=[error_closed_write])
    closing.upon(local_close, enter=closing, outputs=[error_closed_close])
    # the CLOSED state shouldn't ever see messages, since we'll be deleted
    # (but a local user should be able to call "close" without having
    # to know what state we're in)
    closed.upon(local_close, enter=closed, outputs=[])

    # our endpoints use these

    def _set_protocol(self, protocol):
        assert not self._protocol
        self._protocol = protocol
        if IHalfCloseableProtocol.providedBy(protocol):
            self.connect_protocol_half()
        else:
            # move from UNCONNECTED to OPEN
            self.connect_protocol_full()

    def _deliver_queued_data(self):
        for data in self._pending_remote_data:
            self.remote_data(data)
        del self._pending_remote_data
        if self._pending_remote_close:
            self.remote_close()
            del self._pending_remote_close

    # ITransport
    def write(self, data):
        assert isinstance(data, type(b""))
        assert len(data) <= MAX_FRAME_LENGTH
        self.local_data(data)

    def writeSequence(self, iovec):
        self.write(b"".join(iovec))

    def loseWriteConnection(self):
        if not IHalfCloseableProtocol.providedBy(self._protocol):
            # this is a clear error
            raise HalfCloseUsedOnNonHalfCloseable()
        self.local_close()

    def loseConnection(self):
        # TODO: what happens if an IHalfCloseableProtocol calls normal
        # loseConnection()? I think we need to close the read side too.
        if IHalfCloseableProtocol.providedBy(self._protocol):
            # I don't know is correct, so avoid this for now
            raise NormalCloseUsedOnHalfCloseable()
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
@attrs
class ControlEndpoint(object):
    _peer_addr = attrib(validator=provides(IAddress))
    _subchannel_zero = attrib(validator=provides(ISubChannel))
    _eventual_queue = attrib(repr=False)
    _used = False

    def __attrs_post_init__(self):
        self._once = Once(SingleUseEndpointError)
        self._wait_for_main_channel = OneShotObserver(self._eventual_queue)

    # from manager

    def _main_channel_ready(self):
        self._wait_for_main_channel.fire(None)

    def _main_channel_failed(self, f):
        self._wait_for_main_channel.error(f)

    @inlineCallbacks
    def connect(self, protocolFactory):
        # return Deferred that fires with IProtocol or Failure(ConnectError)
        self._once()
        yield self._wait_for_main_channel.when_fired()
        p = protocolFactory.buildProtocol(self._peer_addr)
        self._subchannel_zero._set_protocol(p)
        # this sets p.transport and calls p.connectionMade()
        p.makeConnection(self._subchannel_zero)
        self._subchannel_zero._deliver_queued_data()
        returnValue(p)


@implementer(IStreamClientEndpoint)
@attrs
class SubchannelConnectorEndpoint(object):
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=instance_of(_WormholeAddress))
    _eventual_queue = attrib(repr=False)

    def __attrs_post_init__(self):
        self._connection_deferreds = deque()
        self._wait_for_main_channel = OneShotObserver(self._eventual_queue)

    def _main_channel_ready(self):
        self._wait_for_main_channel.fire(None)

    def _main_channel_failed(self, f):
        self._wait_for_main_channel.error(f)

    @inlineCallbacks
    def connect(self, protocolFactory):
        # return Deferred that fires with IProtocol or Failure(ConnectError)
        yield self._wait_for_main_channel.when_fired()
        scid = self._manager.allocate_subchannel_id()
        self._manager.send_open(scid)
        peer_addr = _SubchannelAddress(scid)
        # ? f.doStart()
        # ? f.startedConnecting(CONNECTOR) # ??
        sc = SubChannel(scid, self._manager, self._host_addr, peer_addr)
        self._manager.subchannel_local_open(scid, sc)
        p = protocolFactory.buildProtocol(peer_addr)
        sc._set_protocol(p)
        p.makeConnection(sc)  # set p.transport = sc and call connectionMade()
        returnValue(p)


@implementer(IStreamServerEndpoint)
@attrs
class SubchannelListenerEndpoint(object):
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib(validator=provides(IAddress))
    _eventual_queue = attrib(repr=False)

    def __attrs_post_init__(self):
        self._once = Once(SingleUseEndpointError)
        self._factory = None
        self._pending_opens = deque()
        self._wait_for_main_channel = OneShotObserver(self._eventual_queue)

    # from manager (actually Inbound)
    def _got_open(self, t, peer_addr):
        if self._factory:
            self._connect(t, peer_addr)
        else:
            self._pending_opens.append((t, peer_addr))

    def _connect(self, t, peer_addr):
        p = self._factory.buildProtocol(peer_addr)
        t._set_protocol(p)
        p.makeConnection(t)
        t._deliver_queued_data()

    def _main_channel_ready(self):
        self._wait_for_main_channel.fire(None)

    def _main_channel_failed(self, f):
        self._wait_for_main_channel.error(f)

    # IStreamServerEndpoint

    @inlineCallbacks
    def listen(self, protocolFactory):
        self._once()
        yield self._wait_for_main_channel.when_fired()
        self._factory = protocolFactory
        while self._pending_opens:
            (t, peer_addr) = self._pending_opens.popleft()
            self._connect(t, peer_addr)
        lp = SubchannelListeningPort(self._host_addr)
        returnValue(lp)


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
