import functools

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
