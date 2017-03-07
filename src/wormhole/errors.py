from __future__ import unicode_literals

class WormholeError(Exception):
    """Parent class for all wormhole-related errors"""

class ServerError(WormholeError):
    """The relay server complained about something we did."""

class Timeout(WormholeError):
    pass

class WelcomeError(WormholeError):
    """
    The relay server told us to signal an error, probably because our version
    is too old to possibly work. The server said:"""
    pass

class LonelyError(WormholeError):
    """wormhole.close() was called before the peer connection could be
    established"""

class WrongPasswordError(WormholeError):
    """
    Key confirmation failed. Either you or your correspondent typed the code
    wrong, or a would-be man-in-the-middle attacker guessed incorrectly. You
    could try again, giving both your correspondent and the attacker another
    chance.
    """
    # or the data blob was corrupted, and that's why decrypt failed
    pass

class KeyFormatError(WormholeError):
    """
    The key you entered contains spaces. Magic-wormhole expects keys to be
    separated by dashes. Please reenter the key you were given separating the
    words with dashes.
    """

class ReflectionAttack(WormholeError):
    """An attacker (or bug) reflected our outgoing message back to us."""

class InternalError(WormholeError):
    """The programmer did something wrong."""

class WormholeClosedError(InternalError):
    """API calls may not be made after close() is called."""

class TransferError(WormholeError):
    """Something bad happened and the transfer failed."""

class NoTorError(WormholeError):
    """--tor was requested, but 'txtorcon' is not installed."""

class NoKeyError(WormholeError):
    """w.derive_key() was called before got_verifier() fired"""

class WormholeClosed(Exception):
    """Deferred-returning API calls errback with WormholeClosed if the
    wormhole was already closed, or if it closes before a real result can be
    obtained."""

