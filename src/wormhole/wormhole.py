from __future__ import print_function, absolute_import, unicode_literals
import os, sys
from attr import attrs, attrib
from zope.interface import implementer
from twisted.internet import defer
from ._interfaces import IWormhole
from .util import bytes_to_hexstr
from .timing import DebugTiming
from .journal import ImmediateJournal
from ._boss import Boss

# We can provide different APIs to different apps:
# * Deferreds
#   w.when_got_code().addCallback(print_code)
#   w.send(data)
#   w.receive().addCallback(got_data)
#   w.close().addCallback(closed)

# * delegate callbacks (better for journaled environments)
#   w = wormhole(delegate=app)
#   w.send(data)
#   app.wormhole_got_code(code)
#   app.wormhole_got_verifier(verifier)
#   app.wormhole_receive(data)
#   w.close()
#   app.wormhole_closed()
#
# * potential delegate options
#   wormhole(delegate=app, delegate_prefix="wormhole_",
#            delegate_args=(args, kwargs))

@attrs
@implementer(IWormhole)
class _DelegatedWormhole(object):
    _delegate = attrib()

    def _set_boss(self, boss):
        self._boss = boss

    # from above
    def send(self, plaintext):
        self._boss.send(plaintext)
    def close(self):
        self._boss.close()

    # from below
    def got_code(self, code):
        self._delegate.wormhole_got_code(code)
    def got_verifier(self, verifier):
        self._delegate.wormhole_got_verifier(verifier)
    def received(self, phase, plaintext):
        # TODO: deliver phases in order
        self._delegate.wormhole_received(phase, plaintext)
    def closed(self, result):
        self._delegate.wormhole_closed(result)

@implementer(IWormhole)
class _DeferredWormhole(object):
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

    def send(self, plaintext):
        self._boss.send(plaintext)
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

def _wormhole(appid, relay_url, reactor, delegate=None,
              tor_manager=None, timing=None,
              journal=None,
              stderr=sys.stderr,
              ):
    timing = timing or DebugTiming()
    code_length = 2
    side = bytes_to_hexstr(os.urandom(5))
    journal = journal or ImmediateJournal()
    if delegate:
        w = _DelegatedWormhole(delegate)
    else:
        w = _DeferredWormhole()
    b = Boss(w, side, relay_url, appid, reactor, journal, timing)
    w._set_boss(b)
    # force allocate for now
    b.start()
    b.allocate(code_length)
    return w

def delegated_wormhole(appid, relay_url, reactor, delegate,
                       tor_manager=None, timing=None,
                       journal=None,
                       stderr=sys.stderr,
                       ):
    assert delegate
    return _wormhole(appid, relay_url, reactor, delegate,
                     tor_manager, timing, journal, stderr)

def deferred_wormhole(appid, relay_url, reactor,
                       tor_manager=None, timing=None,
                       journal=None,
                       stderr=sys.stderr,
                       ):
    return _wormhole(appid, relay_url, reactor, None,
                     tor_manager, timing, journal, stderr)
