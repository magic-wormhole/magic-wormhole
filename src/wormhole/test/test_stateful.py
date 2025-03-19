from hypothesis.stateful import rule, precondition, RuleBasedStateMachine
from hypothesis.strategies import integers, lists
from hypothesis import given
import pytest_twisted

client_to_mailbox = [
    {"type": "claim", },
    {"type": "allocate", },
    {"type": "open", "mailbox_id": None},
    {"type": "add", },
    {"type": "release", },
    {"type": "close", "mailbox_id": None, "mood": None},
]

mailbox_to_client = [
    {"type": "welcome", },
    {"type": "claimed", },
    {"type": "allocated", },
    {"type": "opened", },
    {"type": "nameplates", },
    {"type": "ack", },
    {"type": "error", },
    {"type": "message", "side": None, "phase": None},
    {"type": "released", },
    {"type": "closed", },
]

class WormholeMachine(RuleBasedStateMachine):
    def __init__(self,wormholeplz):
        self.wormhole = wormholeplz

    @rule() # how to connect to welcome?
    def new_wormhole(self):
        print("no, really! it happened!")
        assert self.wormhole._B is not None

    @rule()
    @precondition(lambda self: self.wormhole) # can't run this transition/check until we have a wormhole
    def welcome(self):
        # we haven't recv'd a welcome yet
        d = self.wormhole.get_welcome() # we extract a deferred that will be called when we get a welcome message
        assert not d.called # on a deferred there's a "called"
        self.wormhole.tx_welcome() # ok, do it!
        assert d.called # now we have a welcome message!


def test_foo(wormhole):
    t = WormholeMachine(wormhole).TestCase()
    t.run()
