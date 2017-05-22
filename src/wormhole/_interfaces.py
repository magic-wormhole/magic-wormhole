from zope.interface import Interface
from typing import Any; del Any

class IWormhole(Interface):
    pass
class IBoss(Interface):
    pass
class INameplate(Interface):
    pass
class IMailbox(Interface):
    pass
class ISend(Interface):
    pass
class IOrder(Interface):
    pass
class IKey(Interface):
    def __init__(self, other): # type: (Any) -> None
        pass
    def got_pake(self, bytes): # type: (bytes) -> None
        pass
class IReceive(Interface):
    def __init__(self, other): # type: (Any) -> None
        pass
    def got_message(self, side, phase, body): # type: (str, str, bytes) -> None
        pass

class IRendezvousConnector(Interface):
    pass
class ILister(Interface):
    pass
class ICode(Interface):
    pass
class IInput(Interface):
    pass
class IAllocator(Interface):
    pass
class ITerminator(Interface):
    pass

class ITiming(Interface):
    pass
class ITorManager(Interface):
    pass
class IWordlist(Interface):
    def choose_words(length):
        """Randomly select LENGTH words, join them with hyphens, return the
        result."""
    def get_completions(prefix):
        """Return a list of all suffixes that could complete the given
        prefix."""

class IJournal(Interface): # TODO: this needs to be public
    pass
