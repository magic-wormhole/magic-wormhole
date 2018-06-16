from __future__ import unicode_literals


class WormholeError(Exception):
    """Parent class for all wormhole-related errors"""


class UnsendableFileError(Exception):
    """
    A file you wanted to send couldn't be read, maybe because it's not
    a file, or because it's a symlink that points to something
    that doesn't exist.

    To ignore this kind of error, you can run wormhole with the
    --ignore-unsendable-files flag.
    """


class ServerError(WormholeError):
    """The relay server complained about something we did."""


class ServerConnectionError(WormholeError):
    """We had a problem connecting to the relay server:"""

    def __init__(self, url, reason):
        self.url = url
        self.reason = reason

    def __str__(self):
        return str(self.reason)


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
    The key you entered contains spaces or was missing a dash. Magic-wormhole
    expects the numerical nameplate and the code words to be separated by
    dashes. Please reenter the key you were given separating the words with
    dashes.
    """


class ReflectionAttack(WormholeError):
    """An attacker (or bug) reflected our outgoing message back to us."""


class InternalError(WormholeError):
    """The programmer did something wrong."""


class TransferError(WormholeError):
    """Something bad happened and the transfer failed."""


class NoTorError(WormholeError):
    """--tor was requested, but 'txtorcon' is not installed."""


class NoKeyError(WormholeError):
    """w.derive_key() was called before got_verifier() fired"""


class OnlyOneCodeError(WormholeError):
    """Only one w.generate_code/w.set_code/w.input_code may be called"""


class MustChooseNameplateFirstError(WormholeError):
    """The InputHelper was asked to do get_word_completions() or
    choose_words() before the nameplate was chosen."""


class AlreadyChoseNameplateError(WormholeError):
    """The InputHelper was asked to do get_nameplate_completions() after
    choose_nameplate() was called, or choose_nameplate() was called a second
    time."""


class AlreadyChoseWordsError(WormholeError):
    """The InputHelper was asked to do get_word_completions() after
    choose_words() was called, or choose_words() was called a second time."""


class AlreadyInputNameplateError(WormholeError):
    """The CodeInputter was asked to do completion on a nameplate, when we
    had already committed to a different one."""


class WormholeClosed(Exception):
    """Deferred-returning API calls errback with WormholeClosed if the
    wormhole was already closed, or if it closes before a real result can be
    obtained."""


class _UnknownPhaseError(Exception):
    """internal exception type, for tests."""


class _UnknownMessageTypeError(Exception):
    """internal exception type, for tests."""
