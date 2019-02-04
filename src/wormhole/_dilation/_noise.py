try:
    from noise.exceptions import NoiseInvalidMessage
except ImportError:
    class NoiseInvalidMessage(Exception):
        pass

try:
    from noise.exceptions import NoiseHandshakeError
except ImportError:
    class NoiseHandshakeError(Exception):
        pass

try:
    from noise.connection import NoiseConnection
except ImportError:
    # allow imports to work on py2.7, even if dilation doesn't
    NoiseConnection = None
