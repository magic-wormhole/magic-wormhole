from __future__ import absolute_import, print_function, unicode_literals

from twisted.trial import unittest

from .. import journal
from .._interfaces import IJournal


class Journal(unittest.TestCase):
    def test_journal(self):
        events = []
        j = journal.Journal(lambda: events.append("checkpoint"))
        self.assert_(IJournal.providedBy(j))

        with j.process():
            j.queue_outbound(events.append, "message1")
            j.queue_outbound(events.append, "message2")
            self.assertEqual(events, [])
        self.assertEqual(events, ["checkpoint", "message1", "message2"])

    def test_immediate(self):
        events = []
        j = journal.ImmediateJournal()
        self.assert_(IJournal.providedBy(j))

        with j.process():
            j.queue_outbound(events.append, "message1")
            self.assertEqual(events, ["message1"])
            j.queue_outbound(events.append, "message2")
            self.assertEqual(events, ["message1", "message2"])
        self.assertEqual(events, ["message1", "message2"])
