from twisted.application import service
from ..const import RELAY

class TwistedInitiator(service.MultiService):
    def __init__(self, appid, data, reactor, relay=RELAY):
        self.appid = appid
        self.data = data
        self.reactor = reactor
        self.relay = relay

    def when_get_code(self):
        pass # return Deferred

    def when_get_data(self):
        pass # return Deferred

class TwistedReceiver(service.MultiService):
    def __init__(self, appid, data, code, reactor, relay=RELAY):
        self.appid = appid
        self.data = data
        self.code = code
        self.reactor = reactor
        self.relay = relay

    def when_get_data(self):
        pass # return Deferred

