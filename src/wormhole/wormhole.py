from __future__ import print_function, absolute_import, unicode_literals
import os, sys
from attr import attrs, attrib
from zope.interface import implementer
from twisted.python import failure
from twisted.internet import defer
from ._interfaces import IWormhole
from .util import bytes_to_hexstr
from .timing import DebugTiming
from .journal import ImmediateJournal
from ._boss import Boss
from ._key import derive_key
from .errors import NoKeyError
from .util import to_bytes

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

def _log(client_name, machine_name, old_state, input, new_state):
    print("%s.%s[%s].%s -> [%s]" % (client_name, machine_name,
                                    old_state, input, new_state))

@attrs
@implementer(IWormhole)
class _DelegatedWormhole(object):
    _delegate = attrib()

    def __attrs_post_init__(self):
        self._key = None

    def _set_boss(self, boss):
        self._boss = boss

    # from above

    def allocate_code(self, code_length=2):
        self._boss.allocate_code(code_length)
    def input_code(self, stdio):
        self._boss.input_code(stdio)
    def set_code(self, code):
        self._boss.set_code(code)

    def serialize(self):
        s = {"serialized_wormhole_version": 1,
             "boss": self._boss.serialize(),
             }
        return s

    def send(self, plaintext):
        self._boss.send(plaintext)

    def derive_key(self, purpose, length):
        """Derive a new key from the established wormhole channel for some
        other purpose. This is a deterministic randomized function of the
        session key and the 'purpose' string (unicode/py3-string). This
        cannot be called until when_verifier() has fired, nor after close()
        was called.
        """
        if not isinstance(purpose, type("")): raise TypeError(type(purpose))
        if not self._key: raise NoKeyError()
        return derive_key(self._key, to_bytes(purpose), length)

    def close(self):
        self._boss.close()

    def debug_set_trace(self, client_name, which="B N M S O K R RC NL C T",
                           logger=_log):
        self._boss.set_trace(client_name, which, logger)

    # from below
    def got_code(self, code):
        self._delegate.wormhole_got_code(code)
    def got_key(self, key):
        self._key = key # for derive_key()
    def got_verifier(self, verifier):
        self._delegate.wormhole_got_verifier(verifier)
    def received(self, plaintext):
        self._delegate.wormhole_received(plaintext)
    def closed(self, result):
        self._delegate.wormhole_closed(result)

class WormholeClosed(Exception):
    pass

@implementer(IWormhole)
class _DeferredWormhole(object):
    def __init__(self):
        self._code = None
        self._code_observers = []
        self._key = None
        self._verifier = None
        self._verifier_observers = []
        self._received_data = []
        self._received_observers = []
        self._closed_observers = []

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

    def when_received(self):
        if self._received_data:
            return defer.succeed(self._received_data.pop(0))
        d = defer.Deferred()
        self._received_observers.append(d)
        return d

    def allocate_code(self, code_length=2):
        self._boss.allocate_code(code_length)
    def input_code(self, stdio):
        self._boss.input_code(stdio)
    def set_code(self, code):
        self._boss.set_code(code)

    # no .serialize in Deferred-mode
    def send(self, plaintext):
        self._boss.send(plaintext)

    def derive_key(self, purpose, length):
        """Derive a new key from the established wormhole channel for some
        other purpose. This is a deterministic randomized function of the
        session key and the 'purpose' string (unicode/py3-string). This
        cannot be called until when_verifier() has fired, nor after close()
        was called.
        """
        if not isinstance(purpose, type("")): raise TypeError(type(purpose))
        if not self._key: raise NoKeyError()
        return derive_key(self._key, to_bytes(purpose), length)

    def close(self):
        # fails with WormholeError unless we established a connection
        # (state=="happy"). Fails with WrongPasswordError (a subclass of
        # WormholeError) if state=="scary".
        self._boss.close()
        d = defer.Deferred()
        self._closed_observers.append(d)
        return d

    def debug_set_trace(self, client_name, which="B N M S O K R RC L C T",
                           logger=_log):
        self._boss._set_trace(client_name, which, logger)

    # from below
    def got_code(self, code):
        self._code = code
        for d in self._code_observers:
            d.callback(code)
        self._code_observers[:] = []
    def got_key(self, key):
        self._key = key # for derive_key()
    def got_verifier(self, verifier):
        self._verifier = verifier
        for d in self._verifier_observers:
            d.callback(verifier)
        self._verifier_observers[:] = []

    def received(self, plaintext):
        if self._received_observers:
            self._received_observers.pop(0).callback(plaintext)
            return
        self._received_data.append(plaintext)

    def closed(self, result):
        #print("closed", result, type(result))
        if isinstance(result, Exception):
            observer_result = close_result = failure.Failure(result)
        else:
            # pending w.verify() or w.read() get an error
            observer_result = WormholeClosed(result)
            # but w.close() only gets error if we're unhappy
            close_result = result
        for d in self._verifier_observers:
            d.errback(observer_result)
        for d in self._received_observers:
            d.errback(observer_result)
        for d in self._closed_observers:
            d.callback(close_result)

def create(appid, relay_url, reactor, delegate=None, journal=None,
           tor_manager=None, timing=None, stderr=sys.stderr):
    timing = timing or DebugTiming()
    side = bytes_to_hexstr(os.urandom(5))
    journal = journal or ImmediateJournal()
    if delegate:
        w = _DelegatedWormhole(delegate)
    else:
        w = _DeferredWormhole()
    b = Boss(w, side, relay_url, appid, reactor, journal, timing)
    w._set_boss(b)
    b.start()
    return w

def from_serialized(serialized, reactor, delegate,
                    journal=None, tor_manager=None,
                    timing=None, stderr=sys.stderr):
    assert serialized["serialized_wormhole_version"] == 1
    timing = timing or DebugTiming()
    w = _DelegatedWormhole(delegate)
    # now unpack state machines, including the SPAKE2 in Key
    b = Boss.from_serialized(w, serialized["boss"], reactor, journal, timing)
    w._set_boss(b)
    b.start() # ??
    raise NotImplemented
    # should the new Wormhole call got_code? only if it wasn't called before.

# after creating the wormhole object, app must call exactly one of:
# set_code(code), generate_code(), helper=type_code(), and then (if they need
# to know the code) wait for delegate.got_code() or d=w.when_code()

# the helper for type_code() can be asked for completions:
# d=helper.get_completions(text_so_far), which will fire with a list of
# strings that could usefully be appended to text_so_far.

# wormhole.type_code_readline(w) is a wrapper that knows how to use
# w.type_code() to drive rlcompleter

