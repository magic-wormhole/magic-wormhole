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

This library will eventually offer multiple modes. For now, only "transcribe
mode" is available.

Transcribe mode has two variants. In the "machine-generated" variant, the
"initiator" machine creates the invitation code, displays it to the first
user, they convey it (somehow) to the second user, who transcribes it into
the second ("receiver") machine. In the "human-generated" variant, the two
humans come up with the code (possibly without computers), then later
transcribe it into both machines.

When the initiator machine generates the invitation code, the initiator
contacts the rendezvous server and allocates a "channel ID", which is a small
integer. The initiator then displays the invitation code, which is the
channel-ID plus a few secret words. The user copies the code to the second
machine. The receiver machine connects to the rendezvous server, and uses the
invitation code to contact the initiator. They agree upon an encryption key,
and exchange a small encrypted+authenticated data message.

When the humans create an invitation code out-of-band, they are responsible
for choosing an unused channel-ID (simply picking a random 3-or-more digit
number is probably enough), and some random words. The invitation code uses
the same format in either variant: channel-ID, a hyphen, and an arbitrary
string.

The two machines participating in the wormhole setup are not distinguished:
it doesn't matter which one goes first, and both use the same Wormhole class.
In the first variant, one side calls `get_code()` while the other calls
`set_code()`. In the second variant, both sides call `set_code()`. Note that
this is not true for the "Transit" protocol used for bulk data-transfer: the
Transit class currently distinguishes "Sender" from "Receiver", so the
programs on each side must have some way to decide (ahead of time) which is
which.

Each side gets to do one `send_data()` call and one `get_data()` call per
phase (see below). `get_data` will wait until the other side has done
`send_data`, so the application developer must be careful to avoid deadlocks
(don't get before you send on both sides in the same protocol). When both
sides are done, they must call `close()`, to let the library know that the
connection is complete and it can deallocate the channel. If you forget to
call `close()`, the server will not free the channel, and other users will
suffer longer invitation codes as a result. To encourage `close()`, the
library will log an error if a Wormhole object is destroyed before being
closed.

To make it easier to call `close()`, the blocking Wormhole objects can be
used as a context manager. Just put your code in the body of a `with
Wormhole(ARGS) as w:` statement, and `close()` will automatically be called
when the block exits (either successfully or due to an exception).

## Examples

The synchronous+blocking flow looks like this:

```python
from wormhole.blocking.transcribe import Wormhole
from wormhole.public_relay import RENDEZVOUS_RELAY
mydata = b"initiator's data"
with Wormhole(u"appid", RENDEZVOUS_RELAY) as i:
    code = i.get_code()
    print("Invitation Code: %s" % code)
    i.send_data(mydata)
    theirdata = i.get_data()
    print("Their data: %s" % theirdata.decode("ascii"))
```

```python
import sys
from wormhole.blocking.transcribe import Wormhole
from wormhole.public_relay import RENDEZVOUS_RELAY
mydata = b"receiver's data"
code = sys.argv[1]
with Wormhole(u"appid", RENDEZVOUS_RELAY) as r:
    r.set_code(code)
    r.send_data(mydata)
    theirdata = r.get_data()
    print("Their data: %s" % theirdata.decode("ascii"))
```

## Twisted

The Twisted-friendly flow looks like this:

```python
from twisted.internet import reactor
from wormhole.public_relay import RENDEZVOUS_RELAY
from wormhole.twisted.transcribe import Wormhole
outbound_message = b"outbound data"
w1 = Wormhole(u"appid", RENDEZVOUS_RELAY)
d = w1.get_code()
def _got_code(code):
    print "Invitation Code:", code
    return w1.send_data(outbound_message)
d.addCallback(_got_code)
d.addCallback(lambda _: w1.get_data())
def _got_data(inbound_message):
    print "Inbound message:", inbound_message
d.addCallback(_got_data)
d.addCallback(w1.close)
d.addBoth(lambda _: reactor.stop())
reactor.run()
```

On the other side, you call `set_code()` instead of waiting for `get_code()`:

```python
w2 = Wormhole(u"appid", RENDEZVOUS_RELAY)
w2.set_code(code)
d = w2.send_data(my_message)
...
```

Note that the Twisted-form `close()` accepts (and returns) an optional
argument, so you can use `d.addCallback(w.close)` instead of
`d.addCallback(lambda _: w.close())`.

## Phases

If necessary, more than one message can be exchanged through the relay
server. It is not meant as a long-term communication channel, but some
protocols work better if they can exchange an initial pair of messages
(perhaps offering some set of negotiable capabilities), and then follow up
with a second pair (to reveal the results of the negotiation).

To support this, `send_data()/get_data()` accept a "phase" argument: an
arbitrary (unicode) string. It must match the other side: calling
`send_data(data, phase=u"offer")` on one side will deliver that data to
`get_data(phase=u"offer")` on the other.

It is a UsageError to call `send_data()` or `get_data()` twice with the same
phase name. The relay server may limit the number of phases that may be
exchanged, however it will always allow at least two.

## Verifier

You can call `w.get_verifier()` before `send_data()/get_data()`: this will
perform the first half of the PAKE negotiation, then return a verifier object
(bytes) which can be converted into a printable representation and manually
compared. When the users are convinced that `get_verifier()` from both sides
are the same, call `send_data()/get_data()` to continue the transfer. If you
call `send_data()/get_data()` before `get_verifier()`, it will perform the
complete transfer without pausing.

The Twisted form of `get_verifier()` returns a Deferred that fires with the
verifier bytes.

## Generating the Invitation Code

In most situations, the "sending" or "initiating" side will call
`i.get_code()` to generate the invitation code. This returns a string in the
form `NNN-code-words`. The numeric "NNN" prefix is the "channel id", and is a
short integer allocated by talking to the rendezvous server. The rest is a
randomly-generated selection from the PGP wordlist, providing a default of 16
bits of entropy. The initiating program should display this code to the user,
who should transcribe it to the receiving user, who gives it to the Receiver
object by calling `r.set_code()`. The receiving program can also use
`input_code_with_completion()` to use a readline-based input function: this
offers tab completion of allocated channel-ids and known codewords.

Alternatively, the human users can agree upon an invitation code themselves,
and provide it to both programs later (with `i.set_code()` and
`r.set_code()`). They should choose a channel-id that is unlikely to already
be in use (3 or more digits are recommended), append a hyphen, and then
include randomly-selected words or characters. Dice, coin flips, shuffled
cards, or repeated sampling of a high-resolution stopwatch are all useful
techniques.

Note that the code is a human-readable string (the python "unicode" type in
python2, "str" in python3).

## Application Identifier

Applications using this library must provide an "application identifier", a
simple string that distinguishes one application from another. To ensure
uniqueness, use a domain name. To use multiple apps for a single domain,
append a URL-like slash and path, like `example.com/app1`. This string must
be the same on both clients, otherwise they will not see each other. The
invitation codes are scoped to the app-id. Note that the app-id must be
unicode, not bytes, so on python2 use `u"appid"`.

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
This URL is passed as a unicode string.

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

You may not be able to hold the Wormhole object in memory for the whole sync
process: maybe you allow it to wait for several days, but the program will be
restarted during that time. To support this, you can persist the state of the
object by calling `data = w.serialize()`, which will return a printable
bytestring (the JSON-encoding of a small dictionary). To restore, use the
`from_serialized(data)` classmethod (e.g. `w =
Wormhole.from_serialized(data)`).

There is exactly one point at which you can serialize the wormhole: *after*
establishing the invitation code, but before waiting for `get_verifier()` or
`get_data()`, or calling `send_data()`. If you are creating a new invitation
code, the correct time is during the callback fired by `get_code()`. If you
are accepting a pre-generated code, the time is just after calling
`set_code()`.

To properly checkpoint the process, you should store the first message
(returned by `start()`) next to the serialized wormhole instance, so you can
re-send it if necessary.

## Bytes, Strings, Unicode, and Python 3

All cryptographically-sensitive parameters are passed as bytes ("str" in
python2, "bytes" in python3):

* verifier string
* data in/out
* transit records in/out

Other (human-facing) values are always unicode ("unicode" in python2, "str"
in python3):

* wormhole code
* relay URL
* transit URLs
* transit connection hints (e.g. "host:port")
* application identifier
* derived-key "purpose" string: `w.derive_key(PURPOSE)`

## Detailed Example

```python

```
