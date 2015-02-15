
class TransitSender:
    def __init__(self, IDS):
        pass
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
    def __init__(self, IDS):
        pass
    def get_direct_hints(self):
        pass
    def add_sender_direct_hints(self, hints):
        self.sender_direct_hints = hints
    def add_sender_relay_hints(self, hints):
        self.sender_relay_hints = hints
    def establish_connection(self):
        pass
    def receive(self):
        pass
