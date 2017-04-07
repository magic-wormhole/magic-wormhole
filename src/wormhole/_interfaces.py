from zope.interface import Interface

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
    pass
class IReceive(Interface):
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
