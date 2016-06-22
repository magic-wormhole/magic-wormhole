from __future__ import unicode_literals
import functools
import click

class ServerError(Exception):
    def __init__(self, message, relay):
        self.message = message
        self.relay = relay
    def __str__(self):
        return self.message

def handle_server_error(func):
    @functools.wraps(func)
    def _wrap(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ServerError as e:
            print("Server error (from %s):\n%s" % (e.relay, e.message))
            return 1
    return _wrap

class Timeout(Exception):
    pass

class WelcomeError(Exception):
    """
    The relay server told us to signal an error, probably because our version
    is too old to possibly work. The server said:"""
    pass

class WrongPasswordError(Exception):
    """
    Key confirmation failed. Either you or your correspondent typed the code
    wrong, or a would-be man-in-the-middle attacker guessed incorrectly. You
    could try again, giving both your correspondent and the attacker another
    chance.
    """
    # or the data blob was corrupted, and that's why decrypt failed
    pass

class KeyFormatError(Exception):
    """
    The key you entered contains spaces. Magic-wormhole expects keys to be
    separated by dashes. Please reenter the key you were given separating the
    words with dashes.
    """

class ReflectionAttack(Exception):
    """An attacker (or bug) reflected our outgoing message back to us."""

# Click needs to receive click.UsageError instances to "do the right
# thing", which is print the error and exit -- perhaps it would be
# better just to re-export click.UsageError here? Or use
# click.UsageError throughout the codebase?
class UsageError(click.UsageError, Exception):
    """The programmer did something wrong."""

class WormholeClosedError(UsageError):
    """API calls may not be made after close() is called."""

class TransferError(Exception):
    """Something bad happened and the transfer failed."""
