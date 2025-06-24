from collections import namedtuple
from itertools import cycle
from unittest import mock
from zope.interface import alsoProvides
from twisted.internet.task import Clock, Cooperator
from twisted.internet.interfaces import IPullProducer
from ...eventual import EventualQueue
from ..._interfaces import IDilationManager
from ..._dilation.connection import KCM, Open, Data, Close, Ack
from ..._dilation.outbound import Outbound, PullToPush
from .common import clear_mock_calls
import pytest

Pauser = namedtuple("Pauser", ["seqnum"])
NonPauser = namedtuple("NonPauser", ["seqnum"])
Stopper = namedtuple("Stopper", ["sc"])


def make_outbound():
    m = mock.Mock()
    alsoProvides(m, IDilationManager)
    clock = Clock()
    eq = EventualQueue(clock)
    term = mock.Mock(side_effect=lambda: True)  # one write per Eventual tick

    def term_factory():
        return term
    coop = Cooperator(terminationPredicateFactory=term_factory,
                      scheduler=eq.eventually)
    o = Outbound(m, coop)
    c = mock.Mock()  # Connection

    def maybe_pause(r):
        if isinstance(r, Pauser):
            o.pauseProducing()
        elif isinstance(r, Stopper):
            o.subchannel_unregisterProducer(r.sc)
    c.send_record = mock.Mock(side_effect=maybe_pause)
    o._test_eq = eq
    o._test_term = term
    return o, m, c


def test_build_record():
    o, m, c = make_outbound()
    scid1 = b"scid"
    assert o.build_record(Open, scid1, b"proto") == \
                     Open(seqnum=0, scid=b"scid", subprotocol=b"proto")
    assert o.build_record(Data, scid1, b"dataaa") == \
                     Data(seqnum=1, scid=b"scid", data=b"dataaa")
    assert o.build_record(Close, scid1) == \
                     Close(seqnum=2, scid=b"scid")
    assert o.build_record(Close, scid1) == \
                     Close(seqnum=3, scid=b"scid")

def test_outbound_queue():
    o, m, c = make_outbound()
    scid1 = b"scid"
    r1 = o.build_record(Open, scid1, b"proto")
    r2 = o.build_record(Data, scid1, b"data1")
    r3 = o.build_record(Data, scid1, b"data2")
    o.queue_and_send_record(r1)
    o.queue_and_send_record(r2)
    o.queue_and_send_record(r3)
    assert list(o._outbound_queue) == [r1, r2, r3]

    # we would never normally receive an ACK without first getting a
    # connection
    o.handle_ack(r2.seqnum)
    assert list(o._outbound_queue) == [r3]

    o.handle_ack(r3.seqnum)
    assert list(o._outbound_queue) == []

    o.handle_ack(r3.seqnum)  # ignored
    assert list(o._outbound_queue) == []

    o.handle_ack(r1.seqnum)  # ignored
    assert list(o._outbound_queue) == []

def test_duplicate_registerProducer():
    o, m, c = make_outbound()
    sc1 = object()
    p1 = mock.Mock()
    o.subchannel_registerProducer(sc1, p1, True)
    with pytest.raises(ValueError) as ar:
        o.subchannel_registerProducer(sc1, p1, True)
    s = str(ar.value)
    assert "registering producer" in s
    assert "before previous one" in s
    assert "was unregistered" in s

def test_connection_send_queued_unpaused():
    o, m, c = make_outbound()
    scid1 = b"scid"
    r1 = o.build_record(Open, scid1, b"proto")
    r2 = o.build_record(Data, scid1, b"data1")
    r3 = o.build_record(Data, scid1, b"data2")
    o.queue_and_send_record(r1)
    o.queue_and_send_record(r2)
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == []

    # as soon as the connection is established, everything is sent
    o.use_connection(c)
    assert c.mock_calls == [mock.call.transport.registerProducer(o, True),
                                    mock.call.send_record(r1),
                                    mock.call.send_record(r2)]
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == []
    clear_mock_calls(c)

    o.queue_and_send_record(r3)
    assert list(o._outbound_queue) == [r1, r2, r3]
    assert list(o._queued_unsent) == []
    assert c.mock_calls == [mock.call.send_record(r3)]

def test_connection_send_queued_paused():
    o, m, c = make_outbound()
    r1 = Pauser(seqnum=1)
    r2 = Pauser(seqnum=2)
    r3 = Pauser(seqnum=3)
    o.queue_and_send_record(r1)
    o.queue_and_send_record(r2)
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == []

    # pausing=True, so our mock Manager will pause the Outbound producer
    # after each write. So only r1 should have been sent before getting
    # paused
    o.use_connection(c)
    assert c.mock_calls == [mock.call.transport.registerProducer(o, True),
                                    mock.call.send_record(r1)]
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == [r2]
    clear_mock_calls(c)

    # Outbound is responsible for sending all records, so when Manager
    # wants to send a new one, and Outbound is still in the middle of
    # draining the beginning-of-connection queue, the new message gets
    # queued behind the rest (in addition to being queued in
    # _outbound_queue until an ACK retires it).
    o.queue_and_send_record(r3)
    assert list(o._outbound_queue) == [r1, r2, r3]
    assert list(o._queued_unsent) == [r2, r3]
    assert c.mock_calls == []

    o.handle_ack(r1.seqnum)
    assert list(o._outbound_queue) == [r2, r3]
    assert list(o._queued_unsent) == [r2, r3]
    assert c.mock_calls == []

def test_premptive_ack():
    # one mode I have in mind is for each side to send an immediate ACK,
    # with everything they've ever seen, as the very first message on each
    # new connection. The idea is that you might preempt sending stuff from
    # the _queued_unsent list if it arrives fast enough (in practice this
    # is more likely to be delivered via the DILATE mailbox message, but
    # the effects might be vaguely similar, so it seems worth testing
    # here). A similar situation would be if each side sends ACKs with the
    # highest seqnum they've ever seen, instead of merely ACKing the
    # message which was just received.
    o, m, c = make_outbound()
    r1 = Pauser(seqnum=1)
    r2 = Pauser(seqnum=2)
    r3 = Pauser(seqnum=3)
    o.queue_and_send_record(r1)
    o.queue_and_send_record(r2)
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == []

    o.use_connection(c)
    assert c.mock_calls == [mock.call.transport.registerProducer(o, True),
                                    mock.call.send_record(r1)]
    assert list(o._outbound_queue) == [r1, r2]
    assert list(o._queued_unsent) == [r2]
    clear_mock_calls(c)

    o.queue_and_send_record(r3)
    assert list(o._outbound_queue) == [r1, r2, r3]
    assert list(o._queued_unsent) == [r2, r3]
    assert c.mock_calls == []

    o.handle_ack(r2.seqnum)
    assert list(o._outbound_queue) == [r3]
    assert list(o._queued_unsent) == [r3]
    assert c.mock_calls == []

def test_pause():
    o, m, c = make_outbound()
    o.use_connection(c)
    assert c.mock_calls == [mock.call.transport.registerProducer(o, True)]
    assert list(o._outbound_queue) == []
    assert list(o._queued_unsent) == []
    clear_mock_calls(c)

    sc1, sc2, sc3 = object(), object(), object()
    p1, p2, p3 = mock.Mock(name="p1"), mock.Mock(
        name="p2"), mock.Mock(name="p3")

    # we aren't paused yet, since we haven't sent any data
    o.subchannel_registerProducer(sc1, p1, True)
    assert p1.mock_calls == []

    r1 = Pauser(seqnum=1)
    o.queue_and_send_record(r1)
    # now we should be paused
    assert o._paused
    assert c.mock_calls == [mock.call.send_record(r1)]
    assert p1.mock_calls == [mock.call.pauseProducing()]
    clear_mock_calls(p1, c)

    # so an IPushProducer will be paused right away
    o.subchannel_registerProducer(sc2, p2, True)
    assert p2.mock_calls == [mock.call.pauseProducing()]
    clear_mock_calls(p2)

    o.subchannel_registerProducer(sc3, p3, True)
    assert p3.mock_calls == [mock.call.pauseProducing()]
    assert o._paused_producers == set([p1, p2, p3])
    assert list(o._all_producers) == [p1, p2, p3]
    clear_mock_calls(p3)

    # one resumeProducing should cause p1 to get a turn, since p2 was added
    # after we were paused and p1 was at the "end" of a one-element list.
    # If it writes anything, it will get paused again immediately.
    r2 = Pauser(seqnum=2)
    p1.resumeProducing.side_effect = lambda: c.send_record(r2)
    o.resumeProducing()
    assert p1.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p2.mock_calls == []
    assert p3.mock_calls == []
    assert c.mock_calls == [mock.call.send_record(r2)]
    clear_mock_calls(p1, p2, p3, c)
    # p2 should now be at the head of the queue
    assert list(o._all_producers) == [p2, p3, p1]

    # next turn: p2 has nothing to send, but p3 does. we should see p3
    # called but not p1. The actual sequence of expected calls is:
    # p2.resume, p3.resume, pauseProducing, set(p2.pause, p3.pause)
    r3 = Pauser(seqnum=3)
    p2.resumeProducing.side_effect = lambda: None
    p3.resumeProducing.side_effect = lambda: c.send_record(r3)
    o.resumeProducing()
    assert p1.mock_calls == []
    assert p2.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p3.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert c.mock_calls == [mock.call.send_record(r3)]
    clear_mock_calls(p1, p2, p3, c)
    # p1 should now be at the head of the queue
    assert list(o._all_producers) == [p1, p2, p3]

    # next turn: p1 has data to send, but not enough to cause a pause. same
    # for p2. p3 causes a pause
    r4 = NonPauser(seqnum=4)
    r5 = NonPauser(seqnum=5)
    r6 = Pauser(seqnum=6)
    p1.resumeProducing.side_effect = lambda: c.send_record(r4)
    p2.resumeProducing.side_effect = lambda: c.send_record(r5)
    p3.resumeProducing.side_effect = lambda: c.send_record(r6)
    o.resumeProducing()
    assert p1.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p2.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p3.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert c.mock_calls == [mock.call.send_record(r4),
                                    mock.call.send_record(r5),
                                    mock.call.send_record(r6),
                                    ]
    clear_mock_calls(p1, p2, p3, c)
    # p1 should now be at the head of the queue again
    assert list(o._all_producers) == [p1, p2, p3]

    # now we let it catch up. p1 and p2 send non-pausing data, p3 sends
    # nothing.
    r7 = NonPauser(seqnum=4)
    r8 = NonPauser(seqnum=5)
    p1.resumeProducing.side_effect = lambda: c.send_record(r7)
    p2.resumeProducing.side_effect = lambda: c.send_record(r8)
    p3.resumeProducing.side_effect = lambda: None

    o.resumeProducing()
    assert p1.mock_calls == [mock.call.resumeProducing(),
                                     ]
    assert p2.mock_calls == [mock.call.resumeProducing(),
                                     ]
    assert p3.mock_calls == [mock.call.resumeProducing(),
                                     ]
    assert c.mock_calls == [mock.call.send_record(r7),
                                    mock.call.send_record(r8),
                                    ]
    clear_mock_calls(p1, p2, p3, c)
    # p1 should now be at the head of the queue again
    assert list(o._all_producers) == [p1, p2, p3]
    assert not o._paused

    # now a producer disconnects itself (spontaneously, not from inside a
    # resumeProducing)
    o.subchannel_unregisterProducer(sc1)
    assert list(o._all_producers) == [p2, p3]
    assert p1.mock_calls == []
    assert not o._paused

    # and another disconnects itself when called
    p2.resumeProducing.side_effect = lambda: None
    p3.resumeProducing.side_effect = lambda: o.subchannel_unregisterProducer(
        sc3)
    o.pauseProducing()
    o.resumeProducing()
    assert p2.mock_calls == [mock.call.pauseProducing(),
                                     mock.call.resumeProducing()]
    assert p3.mock_calls == [mock.call.pauseProducing(),
                                     mock.call.resumeProducing()]
    clear_mock_calls(p2, p3)
    assert list(o._all_producers) == [p2]
    assert not o._paused

def test_subchannel_closed():
    o, m, c = make_outbound()

    sc1 = mock.Mock()
    p1 = mock.Mock(name="p1")
    o.subchannel_registerProducer(sc1, p1, True)
    assert p1.mock_calls == [mock.call.pauseProducing()]
    clear_mock_calls(p1)

    o.subchannel_closed(1, sc1)
    assert p1.mock_calls == []
    assert list(o._all_producers) == []

    sc2 = mock.Mock()
    o.subchannel_closed(2, sc2)

def test_disconnect():
    o, m, c = make_outbound()
    o.use_connection(c)

    sc1 = mock.Mock()
    p1 = mock.Mock(name="p1")
    o.subchannel_registerProducer(sc1, p1, True)
    assert p1.mock_calls == []
    o.stop_using_connection()
    assert p1.mock_calls == [mock.call.pauseProducing()]

def OFF_test_push_pull(self):
    # use one IPushProducer and one IPullProducer. They should take turns
    o, m, c = make_outbound()
    o.use_connection(c)
    clear_mock_calls(c)

    sc1, sc2 = object(), object()
    p1, p2 = mock.Mock(name="p1"), mock.Mock(name="p2")
    r1 = Pauser(seqnum=1)
    r2 = NonPauser(seqnum=2)

    # we aren't paused yet, since we haven't sent any data
    o.subchannel_registerProducer(sc1, p1, True)  # push
    o.queue_and_send_record(r1)
    # now we're paused
    assert o._paused
    assert c.mock_calls == [mock.call.send_record(r1)]
    assert p1.mock_calls == [mock.call.pauseProducing()]
    assert p2.mock_calls == []
    clear_mock_calls(p1, p2, c)

    p1.resumeProducing.side_effect = lambda: c.send_record(r1)
    p2.resumeProducing.side_effect = lambda: c.send_record(r2)
    o.subchannel_registerProducer(sc2, p2, False)  # pull: always ready

    # p1 is still first, since p2 was just added (at the end)
    assert o._paused
    assert c.mock_calls == []
    assert p1.mock_calls == []
    assert p2.mock_calls == []
    assert list(o._all_producers) == [p1, p2]
    clear_mock_calls(p1, p2, c)

    # resume should send r1, which should pause everything
    o.resumeProducing()
    assert o._paused
    assert c.mock_calls == [mock.call.send_record(r1),
                                    ]
    assert p1.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p2.mock_calls == []
    assert list(o._all_producers) == [p2, p1]  # now p2 is next
    clear_mock_calls(p1, p2, c)

    # next should fire p2, then p1
    o.resumeProducing()
    assert o._paused
    assert c.mock_calls == [mock.call.send_record(r2),
                                    mock.call.send_record(r1),
                                    ]
    assert p1.mock_calls == [mock.call.resumeProducing(),
                                     mock.call.pauseProducing(),
                                     ]
    assert p2.mock_calls == [mock.call.resumeProducing(),
                                     ]
    assert list(o._all_producers) == [p2, p1]  # p2 still at bat
    clear_mock_calls(p1, p2, c)

def test_pull_producer():
    # a single pull producer should write until it is paused, rate-limited
    # by the cooperator (so we'll see back-to-back resumeProducing calls
    # until the Connection is paused, or 10ms have passed, whichever comes
    # first, and if it's stopped by the timer, then the next EventualQueue
    # turn will start it off again)

    o, m, c = make_outbound()
    eq = o._test_eq
    o.use_connection(c)
    clear_mock_calls(c)
    assert not o._paused

    sc1 = mock.Mock()
    p1 = mock.Mock(name="p1")
    alsoProvides(p1, IPullProducer)

    records = [NonPauser(seqnum=1)] * 10
    records.append(Pauser(seqnum=2))
    records.append(Stopper(sc1))
    it = iter(records)
    p1.resumeProducing.side_effect = lambda: c.send_record(next(it))
    o.subchannel_registerProducer(sc1, p1, False)
    eq.flush_sync()  # fast forward into the glorious (paused) future

    assert o._paused
    assert c.mock_calls == \
                     [mock.call.send_record(r) for r in records[:-1]]
    assert p1.mock_calls == \
                     [mock.call.resumeProducing()] * (len(records) - 1)
    clear_mock_calls(c, p1)

    # next resumeProducing should cause it to disconnect
    o.resumeProducing()
    eq.flush_sync()
    assert c.mock_calls == [mock.call.send_record(records[-1])]
    assert p1.mock_calls == [mock.call.resumeProducing()]
    assert len(o._all_producers) == 0
    assert not o._paused

def test_two_pull_producers():
    # we should alternate between them until paused
    p1_records = ([NonPauser(seqnum=i) for i in range(5)] +
                  [Pauser(seqnum=5)] +
                  [NonPauser(seqnum=i) for i in range(6, 10)])
    p2_records = ([NonPauser(seqnum=i) for i in range(10, 19)] +
                  [Pauser(seqnum=19)])
    expected1 = [NonPauser(0), NonPauser(10),
                 NonPauser(1), NonPauser(11),
                 NonPauser(2), NonPauser(12),
                 NonPauser(3), NonPauser(13),
                 NonPauser(4), NonPauser(14),
                 Pauser(5)]
    expected2 = [NonPauser(15),
                 NonPauser(6), NonPauser(16),
                 NonPauser(7), NonPauser(17),
                 NonPauser(8), NonPauser(18),
                 NonPauser(9), Pauser(19),
                 ]

    o, m, c = make_outbound()
    eq = o._test_eq
    o.use_connection(c)
    clear_mock_calls(c)
    assert not o._paused

    sc1 = mock.Mock()
    p1 = mock.Mock(name="p1")
    alsoProvides(p1, IPullProducer)
    it1 = iter(p1_records)
    p1.resumeProducing.side_effect = lambda: c.send_record(next(it1))
    o.subchannel_registerProducer(sc1, p1, False)

    sc2 = mock.Mock()
    p2 = mock.Mock(name="p2")
    alsoProvides(p2, IPullProducer)
    it2 = iter(p2_records)
    p2.resumeProducing.side_effect = lambda: c.send_record(next(it2))
    o.subchannel_registerProducer(sc2, p2, False)

    eq.flush_sync()  # fast forward into the glorious (paused) future

    sends = [mock.call.resumeProducing()]
    assert o._paused
    assert c.mock_calls == \
                     [mock.call.send_record(r) for r in expected1]
    assert p1.mock_calls == 6 * sends
    assert p2.mock_calls == 5 * sends
    clear_mock_calls(c, p1, p2)

    o.resumeProducing()
    eq.flush_sync()
    assert o._paused
    assert c.mock_calls == \
                     [mock.call.send_record(r) for r in expected2]
    assert p1.mock_calls == 4 * sends
    assert p2.mock_calls == 5 * sends
    clear_mock_calls(c, p1, p2)

def test_send_if_connected():
    o, m, c = make_outbound()
    o.send_if_connected(Ack(1))  # not connected yet

    o.use_connection(c)
    o.send_if_connected(KCM())
    assert c.mock_calls == [mock.call.transport.registerProducer(o, True),
                                    mock.call.send_record(KCM())]

def test_tolerate_duplicate_pause_resume():
    o, m, c = make_outbound()
    assert o._paused  # no connection
    o.use_connection(c)
    assert not o._paused
    o.pauseProducing()
    assert o._paused
    o.pauseProducing()
    assert o._paused
    o.resumeProducing()
    assert not o._paused
    o.resumeProducing()
    assert not o._paused

def test_stopProducing():
    o, m, c = make_outbound()
    o.use_connection(c)
    assert not o._paused
    o.stopProducing()  # connection does this before loss
    assert o._paused
    o.stop_using_connection()
    assert o._paused

def test_resume_error(observe_errors):
    o, m, c = make_outbound()
    o.use_connection(c)
    sc1 = mock.Mock()
    p1 = mock.Mock(name="p1")
    alsoProvides(p1, IPullProducer)
    p1.resumeProducing.side_effect = PretendResumptionError
    o.subchannel_registerProducer(sc1, p1, False)
    o._test_eq.flush_sync()
    # the error is supposed to automatically unregister the producer
    assert list(o._all_producers) == []
    observe_errors.flush(PretendResumptionError)


def make_pushpull(pauses):
    p = mock.Mock()
    alsoProvides(p, IPullProducer)
    unregister = mock.Mock()

    clock = Clock()
    eq = EventualQueue(clock)
    term = mock.Mock(side_effect=lambda: True)  # one write per Eventual tick

    def term_factory():
        return term
    coop = Cooperator(terminationPredicateFactory=term_factory,
                      scheduler=eq.eventually)
    pp = PullToPush(p, unregister, coop)

    it = cycle(pauses)

    def action(i):
        if isinstance(i, Exception):
            raise i
        elif i:
            pp.pauseProducing()
    p.resumeProducing.side_effect = lambda: action(next(it))
    return p, unregister, pp, eq


class PretendResumptionError(Exception):
    pass


class PretendUnregisterError(Exception):
    pass


def test_start_unpaused():
    p, unr, pp, eq = make_pushpull([True])  # pause on each resumeProducing
    # if it starts unpaused, it gets one write before being halted
    pp.startStreaming(False)
    eq.flush_sync()
    assert p.mock_calls == [mock.call.resumeProducing()] * 1
    clear_mock_calls(p)

    # now each time we call resumeProducing, we should see one delivered to
    # the underlying IPullProducer
    pp.resumeProducing()
    eq.flush_sync()
    assert p.mock_calls == [mock.call.resumeProducing()] * 1

    pp.stopStreaming()
    pp.stopStreaming()  # should tolerate this

def test_start_unpaused_two_writes():
    p, unr, pp, eq = make_pushpull([False, True])  # pause every other time
    # it should get two writes, since the first didn't pause
    pp.startStreaming(False)
    eq.flush_sync()
    assert p.mock_calls == [mock.call.resumeProducing()] * 2

def test_start_paused():
    p, unr, pp, eq = make_pushpull([True])  # pause on each resumeProducing
    pp.startStreaming(True)
    eq.flush_sync()
    assert p.mock_calls == []
    pp.stopStreaming()

def test_stop():
    p, unr, pp, eq = make_pushpull([True])
    pp.startStreaming(True)
    pp.stopProducing()
    eq.flush_sync()
    assert p.mock_calls == [mock.call.stopProducing()]


def test_error(observe_errors):
    p, unr, pp, eq = make_pushpull([PretendResumptionError()])
    unr.side_effect = lambda: pp.stopStreaming()
    pp.startStreaming(False)
    eq.flush_sync()
    assert unr.mock_calls == [mock.call()]
    observe_errors.flush(PretendResumptionError)


def test_error_during_unregister(observe_errors):
    p, unr, pp, eq = make_pushpull([PretendResumptionError()])
    unr.side_effect = PretendUnregisterError()
    pp.startStreaming(False)
    eq.flush_sync()
    assert unr.mock_calls == [mock.call()]
    observe_errors.flush(PretendResumptionError)
    observe_errors.flush(PretendUnregisterError)

    # TODO: consider making p1/p2/p3 all elements of a shared Mock, maybe I
    # could capture the inter-call ordering that way
