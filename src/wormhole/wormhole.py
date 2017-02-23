from __future__ import print_function, absolute_import, unicode_literals
import sys
from .timing import DebugTiming
from .journal import ImmediateJournal

def wormhole(appid, relay_url, reactor, tor_manager=None, timing=None,
             stderr=sys.stderr):
    timing = timing or DebugTiming()
    w = _Wormhole(appid, relay_url, reactor, tor_manager, timing, stderr)
    w._start()
    return w

#def wormhole_from_serialized(data, reactor, timing=None):
#    timing = timing or DebugTiming()
#    w = _Wormhole.from_serialized(data, reactor, timing)
#    return w


# considerations for activity management:
# * websocket to server wants to be a t.a.i.ClientService
# * if Wormhole is a MultiService:
#   * makes it easier to chain the ClientService to it
#   * implies that nothing will happen before w.startService()
#   * implies everything stops upon d=w.stopService()
# * if not:
#   * 

class _JournaledWormhole(object):
    def __init__(self, reactor, journal_manager, event_dispatcher,
                 event_dispatcher_args=()):
        pass

class _Wormhole(_JournaledWormhole):
    # send events to self, deliver them via Deferreds
    def __init__(self, reactor):
        _JournaledWormhole.__init__(self, reactor, ImmediateJournal(), self)

def wormhole2(reactor):
    w = _Wormhole(reactor)
    w.startService()
    return w

def journaled_from_data(state, reactor, journal,
                        event_handler, event_handler_args=()):
    pass

def journaled(reactor, journal, event_handler, event_handler_args=()):
    pass
