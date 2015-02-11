# wormhole-sync

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
data = b"initiator's data"
i = Initiator("appid", data)
code = i.get_code()
print("Invitation Code: %s" % code)
theirdata = i.get_data()
print("Their data: %s" % theirdata.decode("ascii"))
```

```python
import sys
from wormhole.transcribe import Receiver
data = b"receiver's data"
code = sys.argv[1]
r = Receiver("appid", code, data)
theirdata = r.get_data()
print("Their data: %s" % theirdata.decode("ascii"))
```

The Twisted-friendly flow looks like this:

```python
from twisted.internet import reactor
from wormhole.transcribe import TwistedInitiator
data = b"initiator's data"
ti = TwistedInitiator("appid", data, reactor)
ti.startService()
d1 = ti.when_get_code()
d1.addCallback(lambda code: print("Invitation Code: %s" % code))
d2 = ti.when_get_data()
d2.addCallback(lambda theirdata:
               print("Their data: %s" % theirdata.decode("ascii")))
d2.addCallback(labmda _: reactor.stop())
reactor.run()
```

```python
from twisted.internet import reactor
from wormhole.transcribe import TwistedReceiver
data = b"receiver's data"
code = sys.argv[1]
tr = TwistedReceiver("appid", code, data, reactor)
tr.startService()
d = tr.when_get_data()
d.addCallback(lambda theirdata:
              print("Their data: %s" % theirdata.decode("ascii")))
d.addCallback(lambda _: reactor.stop())
reactor.run()
```

## Application Identifier

Applications using this library should provide an "application identifier", a
simple bytestring that distinguishes one application from another. To ensure
uniqueness, use a domain name. To use multiple apps for a single domain, just
use a string like `example.com/app1`. This string must be the same on both
clients, otherwise they will not see each other. The invitation codes are
scoped to the app-id.

Distinct app-ids reduce the size of the connection-id numbers. If fewer than
ten initiators are active for a given app-id, the connection-id will only
need to contain a single digit, even if some other app-id is currently using
thousands of concurrent sessions.

## Custom Rendezvous Server

The library uses a baked-in rendezvous server hostname. This must be the same
for both clients. To use a different hostname provide it as the `rendezvous=`
argument to the `Initiator`/`Receiver` constructor.

## Polling and Shutdown

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

You may not be able to hold the Initiator/Receiver object in memory for the
whole sync process: maybe you allow it to wait for several days, but the
program will be restarted during that time. To support this, you can persist
the state of the object by calling `data = i.serialize()`, which will return
a printable bytestring (the JSON-encoding of a small dictionary). To restore,
call `Initiator.from_serialized(data)`.

Note that callbacks are not serialized: they must be restored after
deserialization.

## Detailed Example

```python

```
