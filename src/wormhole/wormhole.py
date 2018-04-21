from __future__ import absolute_import, print_function, unicode_literals

import os
import sys

from attr import attrib, attrs
from twisted.python import failure
from zope.interface import implementer

from ._boss import Boss
from ._interfaces import IDeferredWormhole, IWormhole
from ._key import derive_key
from .errors import NoKeyError, WormholeClosed
from .eventual import EventualQueue
from .journal import ImmediateJournal
from .observer import OneShotObserver, SequenceObserver
from .timing import DebugTiming
from .util import bytes_to_hexstr, to_bytes
from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

# We can provide different APIs to different apps:
# * Deferreds
#   w.get_code().addCallback(print_code)
#   w.send_message(data)
#   w.get_message().addCallback(got_data)
#   w.close().addCallback(closed)

# * delegate callbacks (better for journaled environments)
#   w = wormhole(delegate=app)
#   w.send_message(data)
#   app.wormhole_got_code(code)
#   app.wormhole_got_verifier(verifier)
#   app.wormhole_got_versions(versions)
#   app.wormhole_got_message(data)
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

    def __attrs_post_init__(self):
        self._key = None

    def _set_boss(self, boss):
        self._boss = boss

    # from above

    def allocate_code(self, code_length=2):
        self._boss.allocate_code(code_length)

    def input_code(self):
        return self._boss.input_code()

    def set_code(self, code):
        self._boss.set_code(code)

    # def serialize(self):
    #     s = {"serialized_wormhole_version": 1,
    #          "boss": self._boss.serialize(),
    #          }
    #     return s

    def send_message(self, plaintext):
        self._boss.send(plaintext)

    def derive_key(self, purpose, length):
        """Derive a new key from the established wormhole channel for some
        other purpose. This is a deterministic randomized function of the
        session key and the 'purpose' string (unicode/py3-string). This
        cannot be called until when_verifier() has fired, nor after close()
        was called.
        """
        if not isinstance(purpose, type("")):
            raise TypeError(type(purpose))
        if not self._key:
            raise NoKeyError()
        return derive_key(self._key, to_bytes(purpose), length)

    def close(self):
        self._boss.close()

    def debug_set_trace(self,
                        client_name,
                        which="B N M S O K SK R RC L C T",
                        file=sys.stderr):
        self._boss._set_trace(client_name, which, file)

    # from below
    def got_welcome(self, welcome):
        self._delegate.wormhole_got_welcome(welcome)

    def got_code(self, code):
        self._delegate.wormhole_got_code(code)

    def got_key(self, key):
        self._delegate.wormhole_got_unverified_key(key)
        self._key = key  # for derive_key()

    def got_verifier(self, verifier):
        self._delegate.wormhole_got_verifier(verifier)

    def got_versions(self, versions):
        self._delegate.wormhole_got_versions(versions)

    def received(self, plaintext):
        self._delegate.wormhole_got_message(plaintext)

    def closed(self, result):
        self._delegate.wormhole_closed(result)


@implementer(IWormhole, IDeferredWormhole)
class _DeferredWormhole(object):
    def __init__(self, eq):
        self._welcome_observer = OneShotObserver(eq)
        self._code_observer = OneShotObserver(eq)
        self._key = None
        self._key_observer = OneShotObserver(eq)
        self._verifier_observer = OneShotObserver(eq)
        self._version_observer = OneShotObserver(eq)
        self._received_observer = SequenceObserver(eq)
        self._closed = False
        self._closed_observer = OneShotObserver(eq)

    def _set_boss(self, boss):
        self._boss = boss

    # from above
    def get_code(self):
        # TODO: consider throwing error unless one of allocate/set/input_code
        # was called first. It's legit to grab the Deferred before triggering
        # the process that will cause it to fire, but forbidding that
        # ordering would make it easier to cause programming errors that
        # forget to trigger it entirely.
        return self._code_observer.when_fired()

    def get_welcome(self):
        return self._welcome_observer.when_fired()

    def get_unverified_key(self):
        return self._key_observer.when_fired()

    def get_verifier(self):
        return self._verifier_observer.when_fired()

    def get_versions(self):
        return self._version_observer.when_fired()

    def get_message(self):
        return self._received_observer.when_next_event()

    def allocate_code(self, code_length=2):
        self._boss.allocate_code(code_length)

    def input_code(self):
        return self._boss.input_code()

    def set_code(self, code):
        self._boss.set_code(code)

    # no .serialize in Deferred-mode

    def send_message(self, plaintext):
        self._boss.send(plaintext)

    def derive_key(self, purpose, length):
        """Derive a new key from the established wormhole channel for some
        other purpose. This is a deterministic randomized function of the
        session key and the 'purpose' string (unicode/py3-string). This
        cannot be called until when_verified() has fired, nor after close()
        was called.
        """
        if not isinstance(purpose, type("")):
            raise TypeError(type(purpose))
        if not self._key:
            raise NoKeyError()
        return derive_key(self._key, to_bytes(purpose), length)

    def close(self):
        # fails with WormholeError unless we established a connection
        # (state=="happy"). Fails with WrongPasswordError (a subclass of
        # WormholeError) if state=="scary".
        d = self._closed_observer.when_fired()  # maybe Failure
        if not self._closed:
            self._boss.close()  # only need to close if it wasn't already
        return d

    def debug_set_trace(self,
                        client_name,
                        which="B N M S O K SK R RC L A I C T",
                        file=sys.stderr):
        self._boss._set_trace(client_name, which, file)

    # from below
    def got_welcome(self, welcome):
        self._welcome_observer.fire_if_not_fired(welcome)

    def got_code(self, code):
        self._code_observer.fire_if_not_fired(code)

    def got_key(self, key):
        self._key = key  # for derive_key()
        self._key_observer.fire_if_not_fired(key)

    def got_verifier(self, verifier):
        self._verifier_observer.fire_if_not_fired(verifier)

    def got_versions(self, versions):
        self._version_observer.fire_if_not_fired(versions)

    def received(self, plaintext):
        self._received_observer.fire(plaintext)

    def closed(self, result):
        self._closed = True
        # print("closed", result, type(result), file=sys.stderr)
        if isinstance(result, Exception):
            # everything pending gets an error, including close()
            f = failure.Failure(result)
            self._closed_observer.error(f)
        else:
            # everything pending except close() gets an error:
            # w.get_code()/welcome/unverified_key/verifier/versions/message
            f = failure.Failure(WormholeClosed(result))
            # but w.close() only gets error if we're unhappy
            self._closed_observer.fire_if_not_fired(result)
        self._welcome_observer.error(f)
        self._code_observer.error(f)
        self._key_observer.error(f)
        self._verifier_observer.error(f)
        self._version_observer.error(f)
        self._received_observer.fire(f)


def create(
        appid,
        relay_url,
        reactor,  # use keyword args for everything else
        versions={},
        delegate=None,
        journal=None,
        tor=None,
        timing=None,
        stderr=sys.stderr,
        _eventual_queue=None):
    timing = timing or DebugTiming()
    side = bytes_to_hexstr(os.urandom(5))
    journal = journal or ImmediateJournal()
    eq = _eventual_queue or EventualQueue(reactor)
    if delegate:
        w = _DelegatedWormhole(delegate)
    else:
        w = _DeferredWormhole(eq)
    wormhole_versions = {}  # will be used to indicate Wormhole capabilities
    wormhole_versions["app_versions"] = versions  # app-specific capabilities
    v = __version__
    if isinstance(v, type(b"")):
        v = v.decode("utf-8", errors="replace")
    client_version = ("python", v)
    b = Boss(w, side, relay_url, appid, wormhole_versions, client_version,
             reactor, journal, tor, timing)
    w._set_boss(b)
    b.start()
    return w


# def from_serialized(serialized, reactor, delegate,
#                     journal=None, tor=None,
#                     timing=None, stderr=sys.stderr):
#     assert serialized["serialized_wormhole_version"] == 1
#     timing = timing or DebugTiming()
#     w = _DelegatedWormhole(delegate)
#     # now unpack state machines, including the SPAKE2 in Key
#     b = Boss.from_serialized(w, serialized["boss"], reactor, journal, timing)
#     w._set_boss(b)
#     b.start() # ??
#     raise NotImplemented
#     # should the new Wormhole call got_code? only if it wasn't called before.
