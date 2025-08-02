from collections import deque
from attr import attrs, attrib
from zope.interface import implementer
from twisted.internet.interfaces import IPushProducer, IPullProducer
from twisted.python import log
from twisted.python.reflect import safe_str
from .._interfaces import IDilationManager, IOutbound
from ..util import provides
from .connection import KCM, Ping, Pong, Ack


# Outbound flow control: app writes to subchannel, we write to Connection

# The app can register an IProducer of their choice, to let us throttle their
# outbound data. Not all subchannels will have producers registered, and the
# producer probably won't be the IProtocol instance (it'll be something else
# which feeds data out through the protocol, like a t.p.basic.FileSender). If
# a producerless subchannel writes too much, we won't be able to stop them,
# and we'll keep writing records into the Connection even though it's asked
# us to pause. Likewise, when the connection is down (and we're busily trying
# to reestablish a new one), registered subchannels will be paused, but
# unregistered ones will just dump everything in _outbound_queue, and we'll
# consume memory without bound until they stop.

# We need several things:
#
# * Add each registered IProducer to a list, whose order remains stable. We
#   want fairness under outbound throttling: each time the outbound
#   connection opens up (our resumeProducing method is called), we should let
#   just one producer have an opportunity to do transport.write, and then we
#   should pause them again, and not come back to them until everyone else
#   has gotten a turn. So we want an ordered list of producers to track this
#   rotation.
#
# * Remove the IProducer if/when the protocol uses unregisterProducer
#
# * Remove any registered IProducer when the associated Subchannel is closed.
#   This isn't a problem for normal transports, because usually there's a
#   one-to-one mapping from Protocol to Transport, so when the Transport you
#   forget the only reference to the Producer anyways. Our situation is
#   unusual because we have multiple Subchannels that get merged into the
#   same underlying Connection: each Subchannel's Protocol can register a
#   producer on the Subchannel (which is an ITransport), but that adds it to
#   a set of Producers for the Connection (which is also an ITransport). So
#   if the Subchannel is closed, we need to remove its Producer (if any) even
#   though the Connection remains open.
#
# * Register ourselves as an IPushProducer with each successive Connection
#   object. These connections will come and go, but there will never be more
#   than one. When the connection goes away, pause all our producers. When a
#   new one is established, write all our queued messages, then unpause our
#   producers as we would in resumeProducing.
#
# * Inside our resumeProducing call, we'll cycle through all producers,
#   calling their individual resumeProducing methods one at a time. If they
#   write so much data that the Connection pauses us again, we'll find out
#   because our pauseProducing will be called inside that loop. When that
#   happens, we need to stop looping. If we make it through the whole loop
#   without being paused, then all subchannel Producers are left unpaused,
#   and are free to write whenever they want. During this loop, some
#   Producers will be paused, and others will be resumed
#
# * If our pauseProducing is called, all Producers must be paused, and a flag
#   should be set to notify the resumeProducing loop to exit
#
# * In between calls to our resumeProducing method, we're in one of two
#   states.
#   * If we're writing data too fast, then we'll be left in the "paused"
#     state, in which all Subchannel producers are paused, and the aggregate
#     is paused too (our Connection told us to pauseProducing and hasn't yet
#     told us to resumeProducing). In this state, activity is driven by the
#     outbound TCP window opening up, which calls resumeProducing and allows
#     (probably just) one message to be sent. We receive pauseProducing in
#     the middle of their transport.write, so the loop exits early, and the
#     only state change is that some other Producer should get to go next
#     time.
#   * If we're writing too slowly, we'll be left in the "unpaused" state: all
#     Subchannel producers are unpaused, and the aggregate is unpaused too
#     (resumeProducing is the last thing we've been told). In this state,
#     activity is driven by the Subchannels doing a transport.write, which
#     queues some data on the TCP connection (and then might call
#     pauseProducing if it's now full).
#
# * We want to guard against:
#
#   * application protocol registering a Producer without first unregistering
#     the previous one
#
#   * application protocols writing data despite being told to pause
#     (Subchannels without a registered Producer cannot be throttled, and we
#     can't do anything about that, but we must also handle the case where
#     they give us a pause switch and then proceed to ignore it)
#
#   * our Connection calling resumeProducing or pauseProducing without an
#     intervening call of the other kind
#
#   * application protocols that don't handle a resumeProducing or
#     pauseProducing call without an intervening call of the other kind (i.e.
#     we should keep track of the last thing we told them, and not repeat
#     ourselves)
#
# * If the Wormhole is closed, all Subchannels should close. This is not our
#   responsibility: it lives in (Manager? Inbound?)
#
# * If we're given an IPullProducer, we should keep calling its
#   resumeProducing until it runs out of data. We still want fairness, so we
#   won't call it a second time until everyone else has had a turn.


# There are a couple of different ways to approach this. The one I've
# selected is:
#
# * keep a dict that maps from Subchannel to Producer, which only contains
#   entries for Subchannels that have registered a producer. We use this to
#   remove Producers when Subchannels are closed
#
# * keep a Deque of Producers. This represents the fair-throttling rotation:
#   the left-most item gets the next upcoming turn, and then they'll be moved
#   to the end of the queue.
#
# * keep a set of IPushProducers which are paused, a second set of
#   IPushProducers which are unpaused, and a third set of IPullProducers
#   (which are always left paused) Enforce the invariant that these three
#   sets are disjoint, and that their union equals the contents of the deque.
#
# * keep a "paused" flag, which is cleared upon entry to resumeProducing, and
#   set upon entry to pauseProducing. The loop inside resumeProducing checks
#   this flag after each call to producer.resumeProducing, to sense whether
#   they used their turn to write data, and if that write was large enough to
#   fill the TCP window. If set, we break out of the loop. If not, we look
#   for the next producer to unpause. The loop finishes when all producers
#   are unpaused (evidenced by the two sets of paused producers being empty)
#
# * the "paused" flag also determines whether new IPushProducers are added to
#   the paused or unpaused set (IPullProducers are always added to the
#   pull+paused set). If we have any IPullProducers, we're always in the
#   "writing data too fast" state.

# other approaches that I didn't decide to do at this time (but might use in
# the future):
#
# * use one set instead of two. pros: fewer moving parts. cons: harder to
#   spot decoherence bugs like adding a producer to the deque but forgetting
#   to add it to one of the
#
# * use zero sets, and keep the paused-vs-unpaused state in the Subchannel as
#   a visible boolean flag. This conflates Subchannels with their associated
#   Producer (so if we went this way, we should also let them track their own
#   Producer). Our resumeProducing loop ends when 'not any(sc.paused for sc
#   in self._subchannels_with_producers)'. Pros: fewer subchannel->producer
#   mappings lying around to disagree with one another. Cons: exposes a bit
#   too much of the Subchannel internals


@attrs
@implementer(IOutbound, IPushProducer)
class Outbound:
    # Manage outbound data: subchannel writes to us, we write to transport
    _manager = attrib(validator=provides(IDilationManager))
    _cooperator = attrib()

    def __attrs_post_init__(self):
        # _outbound_queue holds all messages we've ever sent but not retired
        self._outbound_queue = deque()
        self._next_outbound_seqnum = 0
        # _queued_unsent are messages to retry with our new connection
        self._queued_unsent = deque()

        # outbound flow control: the Connection throttles our writes
        self._subchannel_producers = {}  # Subchannel -> IProducer
        self._paused = True  # our Connection called our pauseProducing
        self._all_producers = deque()  # rotates, left-is-next
        self._paused_producers = set()
        self._unpaused_producers = set()
        self._check_invariants()

        self._connection = None

    def _check_invariants(self):
        assert self._unpaused_producers.isdisjoint(self._paused_producers)
        assert (self._paused_producers.union(self._unpaused_producers) ==
                set(self._all_producers))

    def build_record(self, record_type, *args):
        seqnum = self._next_outbound_seqnum
        self._next_outbound_seqnum += 1
        r = record_type(seqnum, *args)
        assert hasattr(r, "seqnum"), r  # only Open/Data/Close
        return r

    def queue_and_send_record(self, r):
        # we always queue it, to resend on a subsequent connection if
        # necessary
        self._outbound_queue.append(r)

        if self._connection:
            if self._queued_unsent:
                # to maintain correct ordering, queue this instead of sending it
                self._queued_unsent.append(r)
            else:
                # we're allowed to send it immediately
                self._connection.send_record(r)

    def send_if_connected(self, r):
        assert isinstance(r, (KCM, Ping, Pong, Ack)), r  # nothing with seqnum
        if self._connection:
            self._connection.send_record(r)

    # our subchannels call these to register a producer

    def subchannel_registerProducer(self, sc, producer, streaming):
        # streaming==True: IPushProducer (pause/resume)
        # streaming==False: IPullProducer (just resume)
        if sc in self._subchannel_producers:
            raise ValueError(
                "registering producer %s before previous one (%s) was "
                "unregistered" % (producer,
                                  self._subchannel_producers[sc]))
        # our underlying Connection uses streaming==True, so to make things
        # easier, use an adapter when the Subchannel asks for streaming=False
        if not streaming:
            def unregister():
                self.subchannel_unregisterProducer(sc)
            producer = PullToPush(producer, unregister, self._cooperator)

        self._subchannel_producers[sc] = producer
        self._all_producers.append(producer)
        if self._paused:
            self._paused_producers.add(producer)
        else:
            self._unpaused_producers.add(producer)
        self._check_invariants()
        if streaming:
            if self._paused:
                # IPushProducers need to be paused immediately, before they
                # speak
                producer.pauseProducing()  # you wake up sleeping
        else:
            # our PullToPush adapter must be started, but if we're paused then
            # we tell it to pause before it gets a chance to write anything
            producer.startStreaming(self._paused)

    def subchannel_unregisterProducer(self, sc):
        # TODO: what if the subchannel closes, so we unregister their
        # producer for them, then the application reacts to connectionLost
        # with a duplicate unregisterProducer?
        p = self._subchannel_producers.pop(sc)
        if isinstance(p, PullToPush):
            p.stopStreaming()
        self._all_producers.remove(p)
        self._paused_producers.discard(p)
        self._unpaused_producers.discard(p)
        self._check_invariants()

    def subchannel_closed(self, scid, sc):
        self._check_invariants()
        if sc in self._subchannel_producers:
            self.subchannel_unregisterProducer(sc)

    # our Manager tells us when we've got a new Connection to work with

    def use_connection(self, c):
        self._connection = c
        assert not self._queued_unsent
        self._queued_unsent.extend(self._outbound_queue)
        # the connection can tell us to pause when we send too much data
        c.transport.registerProducer(self, True)  # IPushProducer: pause+resume
        # send our queued messages
        self.resumeProducing()

    def stop_using_connection(self):
        self._connection.transport.unregisterProducer()
        self._connection = None
        self._queued_unsent.clear()
        self.pauseProducing()
        # TODO: I expect this will call pauseProducing twice: the first time
        # when we get stopProducing (since we're registere with the
        # underlying connection as the producer), and again when the manager
        # notices the connectionLost and calls our _stop_using_connection

    def handle_ack(self, resp_seqnum):
        # we've received an inbound ack, so retire something
        while (self._outbound_queue and
               self._outbound_queue[0].seqnum <= resp_seqnum):
            self._outbound_queue.popleft()
        while (self._queued_unsent and
               self._queued_unsent[0].seqnum <= resp_seqnum):
            self._queued_unsent.popleft()
        # Inbound is responsible for tracking the high watermark and deciding
        # whether to ignore inbound messages or not

    # IPushProducer: the active connection calls these because we used
    # c.transport.registerProducer to ask for them

    def pauseProducing(self):
        if self._paused:
            return  # someone is confused and called us twice
        self._paused = True
        for p in self._all_producers:
            if p in self._unpaused_producers:
                self._unpaused_producers.remove(p)
                self._paused_producers.add(p)
                p.pauseProducing()

    def resumeProducing(self):
        if not self._paused:
            return  # someone is confused and called us twice
        self._paused = False

        while not self._paused:
            if self._queued_unsent:
                r = self._queued_unsent.popleft()
                self._connection.send_record(r)
                continue
            p = self._get_next_unpaused_producer()
            if not p:
                break
            self._paused_producers.remove(p)
            self._unpaused_producers.add(p)
            p.resumeProducing()

    def _get_next_unpaused_producer(self):
        self._check_invariants()
        if not self._paused_producers:
            return None
        while True:
            p = self._all_producers[0]
            self._all_producers.rotate(-1)  # p moves to the end of the line
            # the only unpaused Producers are at the end of the list
            assert p in self._paused_producers
            return p

    def stopProducing(self):
        # we'll hopefully have a new connection to work with in the future,
        # so we don't shut anything down. We do pause everyone, though.
        self.pauseProducing()


# modelled after twisted.internet._producer_helper._PullToPush , but with a
# configurable Cooperator, a pause-immediately argument to startStreaming()
@implementer(IPushProducer)
@attrs(eq=False)
class PullToPush:
    _producer = attrib(validator=provides(IPullProducer))
    _unregister = attrib(validator=lambda _a, _b, v: callable(v))
    _cooperator = attrib()
    _finished = False

    def _pull(self):
        while True:
            try:
                self._producer.resumeProducing()
            except Exception:
                log.err(None, "%s failed, producing will be stopped:" %
                        (safe_str(self._producer),))
                try:
                    self._unregister()
                    # The consumer should now call stopStreaming() on us,
                    # thus stopping the streaming.
                except Exception:
                    # Since the consumer blew up, we may not have had
                    # stopStreaming() called, so we just stop on our own:
                    log.err(None, "%s failed to unregister producer:" %
                            (safe_str(self._unregister),))
                    self._finished = True
                    return
            yield None

    def startStreaming(self, paused):
        self._coopTask = self._cooperator.cooperate(self._pull())
        if paused:
            self.pauseProducing()  # timer is scheduled, but task is removed

    def stopStreaming(self):
        if self._finished:
            return
        self._finished = True
        self._coopTask.stop()

    def pauseProducing(self):
        self._coopTask.pause()

    def resumeProducing(self):
        self._coopTask.resume()

    def stopProducing(self):
        self.stopStreaming()
        self._producer.stopProducing()
