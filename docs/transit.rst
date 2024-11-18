Transit Protocol
================

The Transit protocol is responsible for establishing an encrypted
bidirectional record stream between two programs. It must be given a
“transit key” and a set of “hints” which help locate the other end
(which are both delivered by Wormhole).

The protocol tries hard to create a **direct** connection between the
two ends, but if that fails, it uses a centralized relay server to ferry
data between two separate TCP streams (one to each client). Direct
connection hints are used for the first, and relay hints are used for
the second.

The current implementation starts with the following:

-  detect all of the host’s IP addresses
-  listen on a random TCP port
-  offers the (address,port) pairs as hints

The other side will attempt to connect to each of those ports, as well
as listening on its own socket. After a few seconds without success,
they will both connect to a relay server.

Roles
-----

The Transit protocol has pre-defined “Sender” and “Receiver” roles
(unlike Wormhole, which is symmetric/nobody-goes-first). Each connection
must have exactly one Sender and exactly one Receiver.

The connection itself is bidirectional: either side can send or receive
records. However the connection establishment mechanism needs to know
who is in charge, and the encryption layer needs a way to produce
separate keys for each side..

This may be relaxed in the future, much as Wormhole was.

Records
-------

Transit establishes a **record-pipe**, so the two sides can send and
receive whole records, rather than unframed bytes. This is a side-effect
of the encryption (which uses the NaCl “secretbox” function). The
encryption adds 44 bytes of overhead to each record (4-byte length,
24-byte nonce, 32-byte MAC), so you might want to use slightly larger
records for efficiency. The maximum record size is 2^32 bytes (4GiB).
The whole record must be held in memory at the same time, plus its
ciphertext, so very large ciphertexts are not recommended.

Transit provides **confidentiality**, **integrity**, and **ordering** of
records. Passive attackers can only do the following:

-  learn the size and transmission time of each record
-  learn the sending and destination IP addresses

In addition, an active attacker is able to:

-  delay delivery of individual records, while maintaining ordering (if
   they delay record #4, they must delay #5 and later as well)
-  terminate the connection at any time

If either side receives a corrupted or out-of-order record, they drop
the connection. Attackers cannot modify the contents of a record, or
change the order of the records, without being detected and the
connection being dropped. If a record is lost (e.g. the receiver
observes records #1,#2,#4, but not #3), the connection is dropped when
the unexpected sequence number is received.

Handshake
---------

The transit key is used to derive several secondary keys. Two of them
are used as a “handshake”, to distinguish correct Transit connections
from other programs that happen to connect to the Transit sockets by
mistake or malice.

The handshake is also responsible for choosing exactly one TCP
connection to use, even though multiple outbound and inbound connections
are being attempted.

The SENDER-HANDSHAKE is the string ``transit sender %s ready\n\n``, with
the ``%s`` replaced by a hex-encoded 32-byte HKDF derivative of the
transit key, using a “context string” of ``transit_sender``. The
RECEIVER-HANDSHAKE is the same but with ``receiver`` instead of
``sender`` (both for the string and the HKDF context).

The handshake protocol is like this:

-  immediately upon connection establishment, the Sender writes
   SENDER-HANDSHAKE to the socket (regardless of whether the Sender
   initiated the TCP connection, or was listening on a socket and
   accepted the connection)
-  likewise the Receiver immediately writes RECEIVER-HANDSHAKE to either
   kind of socket
-  if the Sender sees anything other than RECEIVER-HANDSHAKE as the
   first bytes on the wire, it hangs up
-  likewise with the Receiver and SENDER-HANDSHAKE
-  if the Sender sees that this is the first connection to get
   RECEIVER-HANDSHAKE, it sends ``go\n``. If some other connection got
   there first, it hangs up (or sends ``nevermind\n`` and then hangs up,
   but this is mostly for debugging, and implementations should not
   depend upon it). After sending ``go``, it switches to
   encrypted-record mode.
-  if the Receiver sees ``go\n``, it switches to encrypted-record mode.
   If the receiver sees anything else, or a disconnected socket, it
   disconnects.

To tolerate the inevitable race conditions created by multiple
contending sockets, only the Sender gets to decide which one wins: the
first one to make it past negotiation. Hopefully this is correlated with
the fastest connection pathway. The protocol ignores any socket that is
not somewhat affiliated with the matching Transit instance.

Hints will frequently point to local IP addresses (local to the other
end) which might be in use by unrelated nearby computers. The handshake
helps to ignore these spurious connections. It is still possible for an
attacker to cause the connection to fail, by intercepting both
connections (to learn the two handshakes), then making new connections
to play back the recorded handshakes, but this level of attacker could
simply drop the user’s packets directly.

Any participant in a Transit connection (i.e. the party on the other end
of your wormhole) can cause their peer to make a TCP connection (and
send the handshake string) to any IP address and port of their choosing.
The handshake protocol is intended to make this no more than a minor
nuisance.

Relay
-----

The **Transit Relay** is a host which offers TURN-like services for
magic-wormhole instances. It uses a TCP-based protocol with a handshake
to determine which connection wants to be connected to which.

When connecting to a relay, the Transit client first writes
RELAY-HANDSHAKE to the socket, which is ``please relay %s\n``, where
``%s`` is the hex-encoded 32-byte HKDF derivative of the transit key,
using ``transit_relay_token`` as the context. The client then waits for
``ok\n``.

The relay waits for a second connection that uses the same token. When
this happens, the relay sends ``ok\n`` to both, then wires the
connections together, so that everything received after the token on one
is written out (after the ok) on the other. When either connection is
lost, the other will be closed (the relay does not support
“half-close”).

When clients use a relay connection, they perform the usual
sender/receiver handshake just after the ``ok\n`` is received: until
that point they pretend the connection doesn’t even exist.

Direct connections are better, since they are faster and less expensive
for the relay operator. If there are any potentially-viable direct
connection hints available, the Transit instance will wait a few seconds
before attempting to use the relay. If it has no viable direct hints, it
will start using the relay right away. This prefers direct connections,
but doesn’t introduce completely unnecessary stalls.

The Transit client can attempt connections to multiple relays, and uses
the first one that passes negotiation. Each side combines a
locally-configured hostname/port (usually “baked in” to the application,
and hosted by the application author) with additional hostname/port
pairs that come from the peer. This way either side can suggest the
relays to use. The ``wormhole`` application accepts a
``--transit-helper tcp:myrelay.example.org:12345`` command-line option
to supply an additional relay. The connection hints provided by the
Transit instance include the locally-configured relay, along with the
dynamically-determined direct hints. Both should be delivered to the
peer.

API
---

The Transit API uses Twisted and returns Deferreds for any call that
cannot be handled immediately. The complete example is here:

.. code:: python

   from twisted.internet.defer import inlineCallbacks
   from wormhole.transit import TransitSender

   @inlineCallbacks
   def do_transit():
       s = TransitSender("tcp:relayhost.example.org:12345")
       my_connection_hints = yield s.get_connection_hints()
       # (send my hints via wormhole)
       # (get their hints via wormhole)
       s.add_connection_hints(their_connection_hints)
       key = w.derive_key(application_id + "/transit-key")
       s.set_transit_key(key)
       rp = yield s.connect()
       rp.send_record(b"my first record")
       their_record = yield rp.receive_record()
       rp.send_record(b"Greatest Hits)
       other = yield rp.receive_record()
       yield rp.close()

First, create a Transit instance, giving it the connection information
of the “baked-in” transit relay. The application must know whether it
should use a Sender or a Receiver:

.. code:: python

   from wormhole.transit import TransitSender
   s = TransitSender(baked_in_relay)

Next, ask the Transit for its direct and relay hints. This should be
delivered to the other side via a Wormhole message (i.e. add them to a
dict, serialize it with JSON, send the result as a message with
``wormhole.send()``). The ``get_connection_hints`` method returns a
Deferred, so in the example we use ``@inlineCallbacks`` to ``yield`` the
result.

.. code:: python

   my_connection_hints = yield s.get_connection_hints()

Then, perform the Wormhole exchange, which ought to give you the direct
and relay hints of the other side. Tell your Transit instance about
their hints.

.. code:: python

   s.add_connection_hints(their_connection_hints)

Then use ``wormhole.derive_key()`` to obtain a shared key for Transit
purposes, and tell your Transit about it. Both sides must use the same
derivation string, and this string must not be used for any other
purpose, but beyond that it doesn’t much matter what the exact
derivation string is. The key is secret, of course.

.. code:: python

   key = w.derive_key(application_id + "/transit-key")
   s.set_transit_key(key)

Finally, tell the Transit instance to connect. This returns a Deferred
that will yield a “record pipe” object, on which records can be sent and
received. If no connection can be established within a timeout (defaults
to 30 seconds), ``connect()`` will signal a Failure instead. The pipe
can be closed with ``close()``, which returns a Deferred that fires when
all data has been flushed.

.. code:: python

   rp = yield s.connect()
   rp.send_record(b"my first record")
   their_record = yield rp.receive_record()
   rp.send_record(b"Greatest Hits)
   other = yield rp.receive_record()
   yield rp.close()

Records can be sent and received in arbitrary order (you are not limited
to taking turns).

The record-pipe object also implements the ``IConsumer``/``IProducer``
protocols for **bytes**, which means you can transfer a file by wiring
up a file reader as a Producer. Each chunk of bytes that the Producer
generates will be put into a single record. The Consumer interface works
the same way. This enables backpressure and flow-control: if the far end
(or the network) cannot keep up with the stream of data, the sender will
wait for them to catch up before filling buffers without bound.
