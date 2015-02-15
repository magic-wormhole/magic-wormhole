import os
from ..util import ipaddrs

class TransitSender:
    def __init__(self):
        self.key = os.urandom(32)
    def get_transit_key(self):
        return self.key
    def get_direct_hints(self):
        pass
    def get_relay_hints(self):
        return []
    def add_receiver_hints(self, hints):
        self.receiver_hints = hints
    def establish_connection(self):
        pass
    def write(self, data):
        pass
    def close(self):
        pass

class TransitReceiver:
    def __init__(self):
        pass
    def get_direct_hints(self):
        pass
    def set_transit_key(self, key):
        self.key = key
    def add_sender_direct_hints(self, hints):
        self.sender_direct_hints = hints
    def add_sender_relay_hints(self, hints):
        self.sender_relay_hints = hints
    def establish_connection(self):
        pass
    def receive(self):
        pass
