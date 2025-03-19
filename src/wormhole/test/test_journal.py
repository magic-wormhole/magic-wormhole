from .. import journal
from .._interfaces import IJournal

import pytest

def test_journal():
    events = []
    j = journal.Journal(lambda: events.append("checkpoint"))
    assert IJournal.providedBy(j), "was not provided"

    with j.process():
        j.queue_outbound(events.append, "message1")
        j.queue_outbound(events.append, "message2")
        assert events == [], "events was not empty"
    assert events == ["checkpoint", "message1", "message2"], "events was not the expected value"

def test_immediate():
    events = []
    j = journal.ImmediateJournal()
    assert IJournal.providedBy(j),"not provided"

    with j.process():
        j.queue_outbound(events.append, "message1")
        assert events == ["message1"],"events not what expected"
        j.queue_outbound(events.append, "message2")
        assert events == ["message1", "message2"]
    assert events == ["message1", "message2"], "events not what expected"
