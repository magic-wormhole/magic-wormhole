# Magic-Wormhole

This library provides a primitive function to securely transfer small amounts
of data between two computers. Both machines must be connected to the
internet, but they do not need to have public IP addresses or know how to
contact each other ahead of time.

Security and connectivity is provided by means of an "invitation code": a
short string that is transcribed from one machine to the other by the users
at the keyboard. This works in conjunction with a baked-in "rendezvous
server" that relays information from one machine to the other.

## Modes

This library will eventually offer multiple modes.

The first mode provided is "transcribe" mode. In this mode, one machine goes
first, and is called the "initiator". The initiator contacts the rendezvous
server and allocates a "channel ID", which is a small integer. The initiator
then displays the "invitation code", which is the channel-ID plus a few
secret words. The user copies the invitation code to the second machine,
called the "receiver". The receiver connects to the rendezvous server, and
uses the invitation code to contact the initiator. They agree upon an
encryption key, and exchange a small encrypted+authenticated data message.

## Examples

The synchronous+blocking flow looks like this:

```python
from wormhole.transcribe import Initiator
from wormhole.public_relay import RENDEZVOUS_RELAY
mydata = b"initiator's data"
i = Initiator("appid", RENDEZVOUS_RELAY)
code = i.get_code()
print("Invitation Code: %s" % code)
theirdata = i.get_data(mydata)
print("Their data: %s" % theirdata.decode("ascii"))
```

```python
import sys
from wormhole.transcribe import Receiver
from wormhole.public_relay import RENDEZVOUS_RELAY
mydata = b"receiver's data"
code = sys.argv[1]
r = Receiver("appid", RENDEZVOUS_RELAY)
r.set_code(code)
theirdata = r.get_data(mydata)
print("Their data: %s" % theirdata.decode("ascii"))
```

## Twisted

The Twisted-friendly flow looks like this:

```python
from twisted.internet import reactor
from wormhole.public_relay import RENDEZVOUS_RELAY
from wormhole.twisted.transcribe import SymmetricWormhole
outbound_message = b"outbound data"
w1 = SymmetricWormhole("appid", RENDEZVOUS_RELAY)
d = w1.get_code()
def _got_code(code):
    print "Invitation Code:", code
    return w1.get_data(outbound_message)
d.addCallback(_got_code)
def _got_data(inbound_message):
    print "Inbound message:", inbound_message
d.addCallback(_got_data)
d.addBoth(lambda _: reactor.stop())
reactor.run()
```

On the other side, you call `set_code()` instead of waiting for `get_code()`:

```python
w2 = SymmetricWormhole("appid", RENDEZVOUS_RELAY)
w2.set_code(code)
d = w2.get_data(my_message)
```

You can call `d=w.get_verifier()` before `get_data()`: this will perform the
first half of the PAKE negotiation, then fire the Deferred with a verifier
object (bytes) which can be converted into a printable representation and
manually compared. When the users are convinced that `get_verifier()` from
both sides are the same, call `d=get_data()` to continue the transfer. If you
call `get_data()` first, it will perform the complete transfer without
pausing.

## Generating the Invitation Code

In most situations, the Initiator will call `i.get_code()` to generate the
invitation code. This returns a string in the form `NNN-code-words`. The
numeric "NNN" prefix is the "channel id", and is a short integer allocated by
talking to the rendezvous server. The rest is a randomly-generated selection
from the PGP wordlist, providing a default of 16 bits of entropy. The
initiating program should display this code to the user, who should
transcribe it to the receiving user, who gives it to the Receiver object by
calling `r.set_code()`. The receiving program can also use
`input_code_with_completion()` to use a readline-based input function: this
offers tab completion of allocated channel-ids and known codewords.

Alternatively, the human users can agree upon an invitation code themselves,
and provide it to both programs later (with `i.set_code()` and
`r.set_code()`). They should choose a channel-id that is unlikely to already
be in use (3 or more digits are recommended), append a hyphen, and then
include randomly-selected words or characters. Dice, coin flips, shuffled
cards, or repeated sampling of a high-resolution stopwatch are all useful
techniques.


## Application Identifier

Applications using this library must provide an "application identifier", a
simple bytestring that distinguishes one application from another. To ensure
uniqueness, use a domain name. To use multiple apps for a single domain, just
use a string like `example.com/app1`. This string must be the same on both
clients, otherwise they will not see each other. The invitation codes are
scoped to the app-id.

Distinct app-ids reduce the size of the connection-id numbers. If fewer than
ten initiators are active for a given app-id, the connection-id will only
need to contain a single digit, even if some other app-id is currently using
thousands of concurrent sessions.

## Rendezvous Relays

The library depends upon a "rendezvous relay", which is a server (with a
public IP address) that delivers small encrypted messages from one client to
the other. This must be the same for both clients, and is generally baked-in
to the application source code or default config.

This library includes the URL of a public relay run by the author.
Application developers can use this one, or they can run their own (see
src/wormhole/servers/relay.py) and configure their clients to use it instead.

## Polling and Shutdown

TODO: this is mostly imaginary

The reactor-based (Twisted-style) forms of these objects need to establish
TCP connections, re-establish them if they are lost, and sometimes (for
transports that don't support long-running connections) poll for new
messages. They may also time out eventually. Longer delays mean less network
traffic, but higher latency.

These timers should be matched to the expectations, and expected behavior, of
your users. In a file-transfer application, where the users are sitting next
to each other, it is appropriate to poll very frequently (perhaps every
500ms) for a few minutes, then give up. In an email-like messaging program
where the introduction is establishing a long-term relationship, and the
program can store any outgoing messages until the connection is established,
it is probably better to poll once a minute for the first few minutes, then
back off to once an hour, and not give up for several days.

The `schedule=` constructor argument establishes the polling schedule. It
should contain a sorted list of (when, interval) tuples (both floats). At
`when` seconds after the first `start()` call, the polling interval will be
set to `interval`.

The `timeout=` argument provides a hard timeout. After this many seconds, the
sync will be abandoned, and all callbacks will errback with a TimeoutError.

Both have defaults suitable for face-to-face realtime setup environments.

## Serialization

TODO: only the Twisted form supports serialization so far

You may not be able to hold the Initiator/Receiver object in memory for the
whole sync process: maybe you allow it to wait for several days, but the
program will be restarted during that time. To support this, you can persist
the state of the object by calling `data = w.serialize()`, which will return
a printable bytestring (the JSON-encoding of a small dictionary). To restore,
use the `from_serialized(data)` classmethod (e.g. `w =
SymmetricWormhole.from_serialized(data)`).

There is exactly one point at which you can serialize the wormhole: *after*
establishing the invitation code, but before waiting for `get_verifier()` or
`get_data()`. If you are creating a new code, the correct time is during the
callback fired by `get_code()`. If you are accepting a pre-generated code,
the time is just after calling `set_code()`.

To properly checkpoint the process, you should store the first message
(returned by `start()`) next to the serialized wormhole instance, so you can
re-send it if necessary.

## Detailed Example

```python

```
