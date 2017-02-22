from zope.interface import Interface

class IWormhole(Interface):
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
class INameplateLister(Interface):
    pass
class ICode(Interface):
    pass

class ITiming(Interface):
    pass

class IJournal(Interface): # TODO: this needs to be public
    pass
