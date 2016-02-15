import re
from binascii import hexlify
from .util.hkdf import HKDF

class TransitError(Exception):
    pass

class BadHandshake(Exception):
    pass

class TransitClosed(TransitError):
    pass

class BadNonce(TransitError):
    pass

# The beginning of each TCP connection consists of the following handshake
# messages. The sender transmits the same text regardless of whether it is on
# the initiating/connecting end of the TCP connection, or on the
# listening/accepting side. Same for the receiver.
#
#  sender -> receiver: transit sender TXID_HEX ready\n\n
#  receiver -> sender: transit receiver RXID_HEX ready\n\n
#
# Any deviations from this result in the socket being closed. The handshake
# messages are designed to provoke an invalid response from other sorts of
# servers (HTTP, SMTP, echo).
#
# If the sender is satisfied with the handshake, and this is the first socket
# to complete negotiation, the sender does:
#
#  sender -> receiver: go\n
#
# and the next byte on the wire will be from the application.
#
# If this is not the first socket, the sender does:
#
#  sender -> receiver: nevermind\n
#
# and closes the socket.

# So the receiver looks for "transit sender TXID_HEX ready\n\ngo\n" and hangs
# up upon the first wrong byte. The sender lookgs for "transit receiver
# RXID_HEX ready\n\n" and then makes a first/not-first decision about sending
# "go\n" or "nevermind\n"+close().

def build_receiver_handshake(key):
    hexid = HKDF(key, 32, CTXinfo=b"transit_receiver")
    return b"transit receiver "+hexlify(hexid)+b" ready\n\n"

def build_sender_handshake(key):
    hexid = HKDF(key, 32, CTXinfo=b"transit_sender")
    return b"transit sender "+hexlify(hexid)+b" ready\n\n"

def build_relay_handshake(key):
    token = HKDF(key, 32, CTXinfo=b"transit_relay_token")
    return b"please relay "+hexlify(token)+b"\n"

# The hint format is: TYPE,VALUE= /^([a-zA-Z0-9]+):(.*)$/ . VALUE depends
# upon TYPE, and it can have more colons in it. For TYPE=tcp (the only one
# currently defined), ADDR,PORT = /^(.*):(\d+)$/ , so ADDR can have colons.
# ADDR can be a hostname, ipv4 dotted-quad, or ipv6 colon-hex. If the hint
# publisher wants anonymity, their only hint's ADDR will end in .onion .

def parse_hint_tcp(hint):
    assert isinstance(hint, type(u""))
    # return tuple or None for an unparseable hint
    mo = re.search(r'^([a-zA-Z0-9]+):(.*)$', hint)
    if not mo:
        print("unparseable hint '%s'" % (hint,))
        return None
    hint_type = mo.group(1)
    if hint_type != "tcp":
        print("unknown hint type '%s' in '%s'" % (hint_type, hint))
        return None
    hint_value = mo.group(2)
    mo = re.search(r'^(.*):(\d+)$', hint_value)
    if not mo:
        print("unparseable TCP hint '%s'" % (hint,))
        return None
    hint_host = mo.group(1)
    try:
        hint_port = int(mo.group(2))
    except ValueError:
        print("non-numeric port in TCP hint '%s'" % (hint,))
        return None
    return hint_host, hint_port
