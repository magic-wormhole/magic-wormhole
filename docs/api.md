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
blob = b"initiator's blob"
i = Initiator("appid", blob)
print("Invitation Code: %s" % i.start()
theirblob = i.finish()
print("Their blob: %s" % theirblob.decode("ascii"))
```

```python
import sys
from wormhole.transcribe import Receiver
blob = b"receiver's blob"
code = sys.argv[1]
r = Receiver("appid", code, blob)
theirblob = r.finish()
print("Their blob: %s" % theirblob.decode("ascii"))
```

The Twisted-friendly flow looks like this:

```python
from wormhole.transcribe import Initiator
blob = b"initiator's blob"
i = Initiator("appid", blob)
d = i.start()
d.addCallback(lambda code: print("Invitation Code: %s" % code))
d.addCallback(lambda _: i.finish())
d.addCallback(lambda theirblob:
              print("Their blob: %s" % theirblob.decode("ascii")))
```

```python
from wormhole.transcribe import Receiver
blob = b"receiver's blob"
code = sys.argv[1]
r = Receiver("appid", code, blob)
d = r.finish()
d.addCallback(lambda theirblob:
              print("Their blob: %s" % theirblob.decode("ascii")))
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
