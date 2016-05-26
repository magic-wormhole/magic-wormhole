# Magic-Wormhole

This library provides a primitive function to securely transfer small amounts
of data between two computers. Both machines must be connected to the
internet, but they do not need to have public IP addresses or know how to
contact each other ahead of time.

Security and connectivity is provided by means of an "invitation code": a
short string that is transcribed from one machine to the other by the users
at the keyboard. This works in conjunction with a baked-in "rendezvous
server" that relays information from one machine to the other.

The "Wormhole" object provides a secure record pipe between any two programs
that use the same wormhole code (and are configured with the same application
ID and rendezvous server). Each side can send multiple messages to the other,
but the encrypted data for all messages must pass through (and be temporarily
stored on) the rendezvous server, which is a shared resource. For this
reason, larger data (including bulk file transfers) should use the Transit
class instead. The Wormhole object has a method to create a Transit object
for this purpose.

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
`set_code()`. In the second variant, both sides call `set_code()`. (Note that
this is not true for the "Transit" protocol used for bulk data-transfer: the
Transit class currently distinguishes "Sender" from "Receiver", so the
programs on each side must have some way to decide ahead of time which is
which).

Each side can then do an arbitrary number of `send()` and `get()` calls.
`send()` writes a message into the channel. `get()` waits for a new message
to be available, then returns it. The Wormhole is not meant as a long-term
communication channel, but some protocols work better if they can exchange an
initial pair of messages (perhaps offering some set of negotiable
capabilities), and then follow up with a second pair (to reveal the results
of the negotiation).

Note: the application developer must be careful to avoid deadlocks (if both
sides want to `get()`, somebody has to `send()` first).

When both sides are done, they must call `close()`, to flush all pending
`send()` calls, deallocate the channel, and close the websocket connection.

## Twisted

The Twisted-friendly flow looks like this (note that passing reactor= is how
you get a non-blocking Wormhole):

```python
from twisted.internet import reactor
from wormhole.public_relay import RENDEZVOUS_RELAY
from wormhole import wormhole
w1 = wormhole(u"appid", RENDEZVOUS_RELAY, reactor)
d = w1.get_code()
def _got_code(code):
    print "Invitation Code:", code
    return w1.send(b"outbound data")
d.addCallback(_got_code)
d.addCallback(lambda _: w1.get())
def _got(inbound_message):
    print "Inbound message:", inbound_message
d.addCallback(_got)
d.addCallback(w1.close)
d.addBoth(lambda _: reactor.stop())
reactor.run()
```

On the other side, you call `set_code()` instead of waiting for `get_code()`:

```python
w2 = wormhole(u"appid", RENDEZVOUS_RELAY, reactor)
w2.set_code(code)
d = w2.send(my_message)
...
```

Note that the Twisted-form `close()` accepts (and returns) an optional
argument, so you can use `d.addCallback(w.close)` instead of
`d.addCallback(lambda _: w.close())`.

## Verifier

For extra protection against guessing attacks, Wormhole can provide a
"Verifier". This is a moderate-length series of bytes (a SHA256 hash) that is
derived from the supposedly-shared session key. If desired, both sides can
display this value, and the humans can manually compare them before allowing
the rest of the protocol to proceed. If they do not match, then the two
programs are not talking to each other (they may both be talking to a
man-in-the-middle attacker), and the protocol should be abandoned.

To retrieve the verifier, you call `d=w.verify()` before any calls to
`send()/get()`. The Deferred will not fire until internal key-confirmation
has taken place (meaning the two sides have exchanged their initial PAKE
messages, and the wormhole codes matched), so `verify()` is also a good way
to detect typos or mistakes entering the code. The Deferred will errback with
wormhole.WrongPasswordError if the codes did not match, or it will callback
with the verifier bytes if they did match.

Once retrieved, you can turn this into hex or Base64 to print it, or render
it as ASCII-art, etc. Once the users are convinced that `verify()` from both
sides are the same, call `send()/get()` to continue the protocol. If you call
`send()/get()` before `verify()`, it will perform the complete protocol
without pausing.

## Generating the Invitation Code

In most situations, the "sending" or "initiating" side will call `get_code()`
to generate the invitation code. This returns a string in the form
`NNN-code-words`. The numeric "NNN" prefix is the "channel id", and is a
short integer allocated by talking to the rendezvous server. The rest is a
randomly-generated selection from the PGP wordlist, providing a default of 16
bits of entropy. The initiating program should display this code to the user,
who should transcribe it to the receiving user, who gives it to the Receiver
object by calling `set_code()`. The receiving program can also use
`input_code()` to use a readline-based input function: this offers tab
completion of allocated channel-ids and known codewords.

Alternatively, the human users can agree upon an invitation code themselves,
and provide it to both programs later (both sides call `set_code()`). They
should choose a channel-id that is unlikely to already be in use (3 or more
digits are recommended), append a hyphen, and then include randomly-selected
words or characters. Dice, coin flips, shuffled cards, or repeated sampling
of a high-resolution stopwatch are all useful techniques.

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
ten Wormholes are active for a given app-id, the connection-id will only need
to contain a single digit, even if some other app-id is currently using
thousands of concurrent sessions.

## Rendezvous Relays

The library depends upon a "rendezvous relay", which is a server (with a
public IP address) that delivers small encrypted messages from one client to
the other. This must be the same for both clients, and is generally baked-in
to the application source code or default config.

This library includes the URL of a public relay run by the author.
Application developers can use this one, or they can run their own (see the
`wormhole-server` command and the `src/wormhole/server/` directory) and
configure their clients to use it instead. This URL is passed as a unicode
string.

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
* derived-key "purpose" string: `w.derive_key(PURPOSE, LENGTH)`
