from __future__ import absolute_import, print_function, unicode_literals

import contextlib

from zope.interface import implementer

from ._interfaces import IJournal


@implementer(IJournal)
class Journal(object):
    def __init__(self, save_checkpoint):
        self._save_checkpoint = save_checkpoint
        self._outbound_queue = []
        self._processing = False

    def queue_outbound(self, fn, *args, **kwargs):
        assert self._processing
        self._outbound_queue.append((fn, args, kwargs))

    @contextlib.contextmanager
    def process(self):
        assert not self._processing
        assert not self._outbound_queue
        self._processing = True
        yield  # process inbound messages, change state, queue outbound
        self._save_checkpoint()
        for (fn, args, kwargs) in self._outbound_queue:
            fn(*args, **kwargs)
        self._outbound_queue[:] = []
        self._processing = False


@implementer(IJournal)
class ImmediateJournal(object):
    def __init__(self):
        pass

    def queue_outbound(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    @contextlib.contextmanager
    def process(self):
        yield
