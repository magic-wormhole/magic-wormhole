from .. import journal
from .._interfaces import IJournal

def test_journal():
    events = []
    j = journal.Journal(lambda: events.append("checkpoint"))
    assert (IJournal.providedBy(j))

    with j.process():
        j.queue_outbound(events.append, "message1")
        j.queue_outbound(events.append, "message2")
        assert events == [],"test_journal: events list is not empty"
    assert events == ["checkpoint", "message1", "message2"]

def test_immediate():
    events = []
    j = journal.ImmediateJournal()
    assert IJournal.providedBy(j)

    with j.process():
        j.queue_outbound(events.append, "message1")
        assert events == ["message1"]
        j.queue_outbound(events.append, "message2")
        assert events == ["message1", "message2"]
    assert events == ["message1", "message2"]
