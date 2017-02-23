from __future__ import print_function, absolute_import, unicode_literals
import sys
from .timing import DebugTiming
from .journal import ImmediateJournal
from ._boss import Boss

class _Wormhole(object):
    def __init__(self):
        self._code = None
        self._code_observers = []
        self._verifier = None
        self._verifier_observers = []

    def _set_boss(self, boss):
        self._boss = boss

    # from above

    def when_code(self):
        if self._code:
            return defer.succeed(self._code)
        d = defer.Deferred()
        self._code_observers.append(d)
        return d

    def when_verifier(self):
        if self._verifier:
            return defer.succeed(self._verifier)
        d = defer.Deferred()
        self._verifier_observers.append(d)
        return d

    def send(self, phase, plaintext):
        self._boss.send(phase, plaintext)
    def close(self):
        self._boss.close()

    # from below
    def got_code(self, code):
        self._code = code
        for d in self._code_observers:
            d.callback(code)
        self._code_observers[:] = []
    def got_verifier(self, verifier):
        self._verifier = verifier
        for d in self._verifier_observers:
            d.callback(verifier)
        self._verifier_observers[:] = []

    def received(self, phase, plaintext):
        print(phase, plaintext)

    def closed(self, result):
        print("closed", result)

def wormhole(appid, relay_url, reactor,
             tor_manager=None, timing=None,
             journal=None,
             stderr=sys.stderr,
             ):
    timing = timing or DebugTiming()
    code_length = 2
    side = bytes_to_hexstr(os.urandom(5))
    journal = journal or ImmediateJournal()
    w = _Wormhole()
    b = Boss(w, side, relay_url, appid, reactor, journal, timing)
    w._set_boss(b)
    # force allocate for now
    b.start()
    b.allocate(code_length)
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
