from zope.interface import implementer
from twisted.application import service
from . import _interfaces

@implementer(_interfaces.IRendezvousConnector)
class RendezvousConnector(service.MultiService, object):
    def __init__(self, journal, timing):
        self._journal = journal
        self._timing = timing

    def wire(self, mailbox, code, nameplate_lister):
        self._M = _interfaces.IMailbox(mailbox)
        self._C = _interfaces.ICode(code)
        self._NL = _interfaces.INameplateListing(nameplate_lister)


    # from Mailbox
    def tx_claim(self):
        pass
    def tx_open(self):
        pass
    def tx_add(self, x):
        pass
    def tx_release(self):
        pass
    def tx_close(self, mood):
        pass
    def stop(self):
        pass

    # from NameplateLister
    def tx_list(self):
        pass

    # from Code
    def tx_allocate(self):
        pass
    
        # record, message, payload, packet, bundle, ciphertext, plaintext
