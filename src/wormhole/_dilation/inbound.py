from attr import attrs, attrib
from zope.interface import implementer
from twisted.python import log
from .._interfaces import IDilationManager, IInbound, ISubChannel
from ..util import provides
from .subchannel import (SubChannel, SubchannelAddress, UnexpectedSubprotocol)


class DuplicateOpenError(Exception):
    pass


class DataForMissingSubchannelError(Exception):
    pass


class CloseForMissingSubchannelError(Exception):
    pass


@attrs
@implementer(IInbound)
class Inbound:
    # Inbound flow control: TCP delivers data to Connection.dataReceived,
    # Connection delivers to our handle_data, we deliver to
    # SubChannel.remote_data, subchannel delivers to proto.dataReceived
    _manager = attrib(validator=provides(IDilationManager))
    _host_addr = attrib()

    def __attrs_post_init__(self):
        # we route inbound Data records to Subchannels .dataReceived
        self._open_subchannels = {}  # scid -> Subchannel
        self._paused_subchannels = set()  # Subchannels that have paused us
        # the set is non-empty, we pause the transport
        self._highest_inbound_acked = -1
        self._connection = None

    # from our Manager
#    def set_listener_endpoint(self, listener_endpoint):
#        self._listener_endpoint = listener_endpoint

    def use_connection(self, c):
        self._connection = c
        # We can pause the connection's reads when we receive too much data. If
        # this is a non-initial connection, then we might already have
        # subchannels that are paused from before, so we might need to pause
        # the new connection before it can send us any data
        if self._paused_subchannels:
            self._connection.pauseProducing()

    def subchannel_local_open(self, scid, sc):
        assert ISubChannel.providedBy(sc)
        assert scid not in self._open_subchannels
        self._open_subchannels[scid] = sc

    # Inbound is responsible for tracking the high watermark and deciding
    # whether to ignore inbound messages or not

    def is_record_old(self, r):
        if r.seqnum <= self._highest_inbound_acked:
            return True
        return False

    def update_ack_watermark(self, seqnum):
        self._highest_inbound_acked = max(self._highest_inbound_acked,
                                          seqnum)

    def handle_open(self, scid, subprotocol):
        log.msg("inbound.handle_open", scid, subprotocol)
        if scid in self._open_subchannels:
            log.err(DuplicateOpenError(
                f"received duplicate OPEN for {scid}"))
            return
        peer_addr = SubchannelAddress(subprotocol)
        sc = SubChannel(scid, self._manager, self._host_addr, peer_addr)
        self._open_subchannels[scid] = sc
        # this can produce a (synchronous) UnexpectedSubprotocol if
        # the user specified "expected subprotocols" but this one
        # isn't in the list.
        try:
            # maybe this should be in Manager?
            self._manager._subprotocol_factories._got_open(sc, peer_addr)
        except UnexpectedSubprotocol:
            self._manager.send_close(scid)
            del self._open_subchannels[scid]

    def handle_data(self, scid, data):
        log.msg("inbound.handle_data", scid, len(data))
        sc = self._open_subchannels.get(scid)
        if sc is None:
            log.err(DataForMissingSubchannelError(
                f"received DATA for non-existent subchannel {scid}"))
            return
        sc.remote_data(data)

    def handle_close(self, scid):
        log.msg("inbound.handle_close", scid)
        sc = self._open_subchannels.get(scid)
        if sc is None:
            log.err(CloseForMissingSubchannelError(
                f"received CLOSE for non-existent subchannel {scid}"))
            return
        sc.remote_close()

    def subchannel_closed(self, scid, sc):
        # connectionLost has just been signalled
        assert self._open_subchannels[scid] is sc
        del self._open_subchannels[scid]

    def stop_using_connection(self):
        self._connection = None

    # from our Subchannel, or rather from the Protocol above it and sent
    # through the subchannel

    # The subchannel is an IProducer, and application protocols can always
    # thell them to pauseProducing if we're delivering inbound data too
    # quickly. They don't need to register anything.

    def subchannel_pauseProducing(self, sc):
        was_paused = bool(self._paused_subchannels)
        self._paused_subchannels.add(sc)
        if self._connection and not was_paused:
            self._connection.pauseProducing()

    def subchannel_resumeProducing(self, sc):
        was_paused = bool(self._paused_subchannels)
        self._paused_subchannels.discard(sc)
        if self._connection and was_paused and not self._paused_subchannels:
            self._connection.resumeProducing()

    def subchannel_stopProducing(self, sc):
        # This protocol doesn't want any additional data. If we were a normal
        # (single-owner) Transport, we'd call .loseConnection now. But our
        # Connection is shared among many subchannels, so instead we just
        # stop letting them pause the connection.
        was_paused = bool(self._paused_subchannels)
        self._paused_subchannels.discard(sc)
        if self._connection and was_paused and not self._paused_subchannels:
            self._connection.resumeProducing()

    # TODO: we might refactor these pause/resume/stop methods by building a
    # context manager that look at the paused/not-paused state first, then
    # lets the caller modify self._paused_subchannels, then looks at it a
    # second time, and calls c.pauseProducing/c.resumeProducing as
    # appropriate. I'm not sure it would be any cleaner, though.
