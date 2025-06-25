The Dilation Protocol
=====================

Dilation takes Magic Wormhole beyond file-transfer!

Designed as the basis for a next-generation file-transfer, Dilation is a “bulk data” protocol between two peers.
Dilation has features suitable for use by a variety of application-level protocols.

.. NOTE::
    Motivational examples / prototypes include `Fowl <https://fowl.readthedocs.io/en/latest/>`_ and applications that use it directly: `Git With Me <https://sr.ht/~meejah/git-withme/>`_, `Pear-On <https://sr.ht/~meejah/pear-on/>`_ and `shwim (Shell With Me) <https://github.com/meejah/shwim>`_.

Dilation is durable and reliable: connections are re-established, and data is definitely transmitted in-order to the other peer.
There are subchannels: logically separate streams as the application protocol requires.

Multiple ways to connect to peers are supported, via “hints”.
Hints currently exist for direct TCP, TCP via Tor, and TCP to a central Transit helper (see also “Canonical hint encodings” in the :doc:`Transit documentation <transit>`.

These building-blocks allow “application” protocols to be simpler by not having to deal with re-connection attempts and network problems.
Dilation was conceived during development of a “next-generation” file-transfer protocol now called “ `Dilated File
Transfer <https://github.com/magic-wormhole/magic-wormhole-protocols/pull/23>`__”.

We aim to make Dilation-using subprotocols *composable*: multiple different Dilation-using subprotocols may work alongside each other over the same connection.

Dilation does *NOT* attempt to replace all manner of peer-to-peer connections; it has enough features to support many use-cases while keeping the simplicity, security and human-involvement of Magic Wormhole's core.

This document assumes you are familiar with the core Mailbox protocol and the general promises of Magic Wormhole.
For more information see :doc:`the Server Protocol <server-protocol>`.


Dilation Overview
-----------------

A slightly deeper dive.

"Dilation" is an optional feature -- you must enable it on your wormhole during the ``create()`` call.
If both peers enable Dilation, then it is available.

Once the wormhole is established, both sides call ``.dilate()`` on their wormhole object.
Only after *both* sides do this is a Dilation peer-to-peer connection established.

The core of Dilation is some number of "subchannels".

Subchannels are created from either peer via a single round-trip over the established connection.
Whenever a subchannel is opened, it must use exactly one *subprotocol* -- if the other peer is listening for this kind of subchannel, it is established.

Subprotocols inherit some features from the overall Dilation structure.
The protocol is already authenticated and end-to-end encrypted.
It is message-based, so no additional framing is required.

The overall Dilation connection is "durable and reliable", which means that once a message is delivered to a Dilation API it will be (eventually) delivered to the peer.
Applications do not need to re-try or re-connect so long as the process keeps running (including changing from wireless to cellular networks, laptop sleeps, intermittent connectivity, or other network weirdness).

In the Python implementation on top of Twisted, we use Twisted APIs -- with the slight refinement that ``dataReceived()`` is called with an entire message.

Twisted "endpoints" are used: client-style when opening a new subchannel, and server-style when awaiting a particular kind of subchannel.
These endpoints are created via the ``DilatedWormhole`` instance returned from the ``dilate()`` call

To initiate an outgoing subchannel, you use the ``DilatedWormhole.connector_for("subproto")`` API to first create a Twisted "client style" endpoint.
Your code would then use ``.connect()`` on the returned object, which will create a ``Protocol`` on your side and initiate the subchannel opening.
The other peer must have called ``DilatedWormhole.listener_for("subproto")`` (and ``.listen()`` with their ``Factory``) for this to work.
That is, for a subprotocol named ``"subproto"``, one side does client-style and one side does server-style Twisted networking.

.. NOTE::

    In an earlier revision of this protocol, there was a special kind of "control" subchannel.
    This was a "singleton" style subchannel (at most one would ever exist).
    Both sides would use the "client-style" endpoint API to create their ``Protocol`` objects.

    We do not currently believe this is necessary -- request/response style protocols work well, and all our example programs exist without a special "control" channel.
    However, we are *open to introducing this in a future revision* of the protocol.

    The only use-case we can think of is when you need an absolute, total ordering of messages sent by both sides.
    If you have a concrete use-case that _can't_ be implemented with the current APIs, **please** get in touch!



Dilation Internals
------------------

This document sometimes mentions programming internals related to Python
and Twisted; these may be ignored by other implementers (see also `the
protocols
repositories <https://github.com/magic-wormhole/magic-wormhole-protocols>`__
for more language-agnostic specifications).

Wormhole Dilation involves several moving parts. Both sides exchange
messages through the Mailbox server to coordinate the establishment of a
more direct connection. This connection might flow in either direction,
so they trade “connection hints” to point at potential listening ports.
This process might succeed in making multiple connections at about the
same time, so one side must select the best one to use, and cleanly shut
down the others. To make the dilated connection *durable*, this side
must also decide when the connection has been lost, and then coordinate
the construction of a replacement. Within this connection, a series of
queued-and-acked subchannel messages are used to open/use/close the
application-visible subchannels.

Versions and can-dilate
~~~~~~~~~~~~~~~~~~~~~~~

The Wormhole protocol includes a ``version`` message sent immediately
after the shared PAKE key is established. This also serves as a
key-confirmation message, allowing each side to confirm that the other
side knows the right key.

The body of the ``version`` message is a JSON-formatted string.
It contains the following keys:

- ``"can-dilate"``: list of strings, each naming a version. Any of these is eligible for use.
  Official versions shall be named after wizard or mage characters from
  the `Earthsea
  <https://en.wikipedia.org/wiki/List_of_characters_in_Earthsea>`_
  series by Ursula le Guin. The current list of valid, supported
  versions is:

  - ``"ged"``: the first version
- ``"dilation-abilities"``: a list of ``dict`` indicating supported
  hint types. Must have a ``"type"`` key, a string the kind of hint.
  Any other keys are ``type``-dependant. Currently valid ``type``s (none of which have additional properties): ``"direct-tcp-v1"``, ``"relay-v1"``.
- ``"app_versions"``: from the ``versions=`` argument to ``wormhole.create()``, an arbitrary JSON-able ``dict``.
  This can be used by application code to negotiate versions, among other uses. In Python, ``IDeferredWormhole.got_versions()`` is called with this ``dict`` (on the peer, and vice-versa).

.. seqdiag::

    seqdiag wormhole {
        Ayo; Mailbox; Brand;
        Ayo -> Brand [label="pake", color=blue]
        Ayo <- Brand [label="pake", color=darkgreen]
        === provisional key established ===
        Ayo <- Brand [label="version:\n can-dilate=[highdrake, ged]", color=darkgreen];
        Ayo -> Brand [label="version:\n can-dilate=[ged]", color=blue];

    }



Leaders and Followers
~~~~~~~~~~~~~~~~~~~~~

Each side of a Wormhole has a randomly-generated Dilation ``side``
string (this is included in the ``please`` message, and is independent
of the Wormhole’s mailbox “side”). When the wormhole is Dilated, the
side with the lexicographically-higher “side” value is named the
“Leader”, and the other side is named the “Follower”. The general
wormhole protocol treats both sides identically, but the distinction
matters for the Dilation protocol. Both sides send a ``please`` as soon
as Dilation is triggered. Each side discovers whether it is the Leader
or the Follower when the peer’s ``please`` arrives. The Leader has
exclusive control over whether a given connection is considered
established or not: if there are multiple potential connections to use,
the Leader decides which one to use, and the Leader gets to decide when
the connection is no longer viable (and triggers the establishment of a
new one).

The ``please`` includes a ``use-version`` key, computed as the “best”
version of the intersection of the two sides’ abilities as reported in
the ``version`` message. Both sides will use whichever
``use-version`` was specified by the Leader (they learn which side is
the Leader at the same moment they learn the peer’s ``use-version``
value). If the Follower cannot handle the ``use-version`` value,
Dilation fails (this should not happen with honest endpoints, as the
Leader knew what the Follower was and was not capable of before
sending that message).

In the example below, ``Brand`` has an experimental version available
in highest position, but ``Ayo`` does not understand that version so they
both pick ``"ged"`` as the version to use.

.. seqdiag::

    seqdiag wormhole {
    Ayo; Mailbox; Brand;

        Ayo -> Brand [label="version:\n can-dilate=[ged]", color=blue];
        Ayo <- Brand [label="version:\n can-dilate=[experiment, ged]", color=darkgreen];

        === have key-confirmation + versions\ndilate() has been called ===

        Ayo -> Brand [label="dilate-0:\n type=please\n side=214fdf39e7ad016f\n use-version=ged", color=blue];
        Ayo <- Brand [label="dilate-1:\n type=please\n side=ff36f931f560e7f5\n use-version=ged", color=darkgreen];
    }

In this illustration, Brand is the leader because their "side" value is higher (that is, ``ff36f931f560e7f5`` is bigger than ``214fdf39e7ad016f``).
They both chose the version ``"ged"`` in this case, but if there was disagreement, the Leader's decision wins.
It is a protocol error if the Follower cannot speak the chosen version (and they should immediately close the Mailbox and disconnect).


Connection Layers
~~~~~~~~~~~~~~~~~

We describe the protocol as a series of layers. Messages sent on one
layer may be encoded or transformed before being delivered on some other
layer.

L1 is the mailbox channel (queued store-and-forward messages that always
go to the mailbox server, and then are forwarded to other clients
subscribed to the same mailbox). Both clients remain connected to the
mailbox server until the Wormhole is closed. They send DILATE-n messages
to each other to manage the Dilation process, including records like
``please``, ``connection-hints``, ``reconnect``, and ``reconnecting``.

L2 is the set of competing connection attempts for a given generation of
connection. Each time the Leader decides to establish a new connection,
a new generation number is used. Hopefully these are direct TCP
connections between the two peers, but they may also include connections
through the transit relay. Each connection must go through an encrypted
handshake process before it is considered viable. Viable connections are
then submitted to a selection process (on the Leader side), which
chooses exactly one to use, and drops the others. It may wait an extra
few seconds in the hopes of getting a “better” connection (faster,
cheaper, etc), but eventually it will select one.

L3 is the current selected connection. There is one L3 for each
generation. At all times, the wormhole will have exactly zero or one L3
connection. L3 is responsible for the selection process, connection
monitoring/keepalives, and serialization/deserialization of the
plaintext frames. L3 delivers decoded frames and
connection-establishment events up to L4.

L4 is the persistent higher-level channel. It is created as soon as the
first L3 connection is selected, and lasts until wormhole is closed
entirely. L4 contains OPEN/DATA/CLOSE/ACK messages: OPEN/DATA/CLOSE have
a sequence number (scoped to the L4 connection and the direction of
travel), and the ACK messages reference those sequence numbers. When a
message is given to the L4 channel for delivery to the remote side, it
is always queued, then transmitted if there is an L3 connection
available. This message remains in the queue until an ACK is received to
retire it. If a new L3 connection is made, all queued messages will be
re-sent (in seqnum order).

L5 are subchannels. There is one pre-established subchannel 0 known as
the “control channel”, which does not require an OPEN message. All other
subchannels are created by the receipt of an OPEN message with the
subchannel number. DATA frames are delivered to a specific subchannel.
When the subchannel is no longer needed, one side will invoke the
``close()`` API (``loseConnection()`` in Twisted), which will cause a
CLOSE message to be sent, and the local L5 object will be put into the
“closing” state. When the other side receives the CLOSE, it will send its
own CLOSE for the same subchannel, and fully close its local object
(``connectionLost()``). When the first side receives CLOSE in the
“closing” state, it will fully close its local object too.

Once a side has sent CLOSE it may not send any more DATA messages.

All L5 subchannels (except the control channel) speak a particular
"subprotocol".  The name of the subprotocol is sent in the OPEN
message. This allows applications to write reusable and composable
subprotocols on top of Dilation.

In Twisted, subprotocols implement the normal ``Factory`` and
``IProtocol`` interfaces (e.g. like TCP streams).  Upon an incoming L5
subchannel open, the Magic Wormhole library invokes the
``buildProtocol`` method on the correct factory, and speaks that
protocol over that subchannel. These are registered via a server-style
endpoint obtained from ``DilatedWormhole.listener_for(...)``.  For
outgoing connections, ``.connect()`` is called with an ``IFactory`` on
the endpoint for that subprotocol (from
``DilatedWormhole.connector_for(...)``).

All L5 subchannels will be paused (``pauseProducing()``) when the L3
connection is paused or lost. They are resumed when the L3 connection is
resumed or reestablished.

Initiating Dilation
-------------------

Dilation is triggered by calling the ``w.dilate()`` API. This
immediately returns a ``DilatedWormhole`` instance. The
``IStreamClientEndpoint`` for a particular subprotocol is obtained via
``DilatedWormhole.connector_for()``. For Dilation to
succeed, both sides must call ``w.dilate()`` at some point.

The client-like endpoints are used to signal any errors that might
prevent Dilation. That is, the ``.connect(factory)`` call returns a
Deferred that will errback (with ``OldPeerCannotDilateError``) if the
other side’s ``version`` message indicates that it does not support
Dilation. The overall dilated connection is durable (the Dilation
agent will try forever to connect, and will automatically reconnect
when necessary), so ``OldPeerCannotDilateError`` is currently the only
error that could be thrown.

If the other side *could* support Dilation (i.e. the wormhole library is
new enough), but the peer does not choose to call ``w.dilate()``, this
Deferred will never fire, and the ``factory`` will never be asked to
create a new ``Protocol`` instance.

The ``dilate()`` call takes an optional ``status_update=`` argument,
which is a callable that receives a single argument: an instance of
``DilationStatus``. This function is called whenever the status
changes (including the overall ``WormholeStatus`` via the ``.mailbox``
member). The information contained in these two objects is intended to
facilitate UX to inform users (e.g. "is it connected?" etc)

The L1 (mailbox) path is used to deliver Dilation requests and
connection hints. The current mailbox protocol uses named “phases” to
distinguish messages (rather than behaving like a regular ordered
channel of arbitrary frames or bytes), and all-number phase names are
reserved for application data (sent via ``w.send_message()``). Therefore
the Dilation control messages use phases named ``DILATE-0``,
``DILATE-1``, etc. Each side maintains its own counter, so one side
might be up to e.g. ``DILATE-5`` while the other has only gotten as far
as ``DILATE-2``. This effectively creates a pair of unidirectional
streams of ``DILATE-n`` messages, each containing one or more Dilation
record, of various types described below. Note that all phases beyond
the initial VERSION and PAKE phases are encrypted by the shared session
key.

A future mailbox protocol might provide a simple ordered stream of typed
messages, with application records and Dilation records mixed together.

Each ``DILATE-n`` message is a JSON-encoded dictionary with a ``type``
field that has a string value. The dictionary will have other keys that
depend upon the type.

``w.dilate()`` triggers transmission of a ``please`` (i.e. “please
dilate”) record with a set of versions that can be accepted. Versions
use strings, rather than integers, to support experimental protocols,
however there is still a total ordering of version preference.

::

   { "type": "please",
     "side": "abcdef",
     "accepted-versions": ["1"]
   }

If one side receives a ``please`` before ``w.dilate()`` has been called
locally, the contents are stored in case ``w.dilate()`` is called in the
future. Once both ``w.dilate()`` has been called and the peer’s
``please`` has been received, the side determines whether it is the
Leader or the Follower. Both sides also compare ``accepted-versions``
fields to choose the best mutually-compatible version to use: they
should always pick the same one.

Then both sides begin the connection process for generation 1 by opening
listening sockets and sending ``connection-hint`` records for each one.
After a slight delay they will also open connections to the Transit
Relay of their choice and produce hints for it too. The receipt of
inbound hints (on both sides) will trigger outbound connection attempts.

Some number of these connections may succeed, and the Leader decides
which to use (via an in-band signal on the established connection). The
others are dropped.

If something goes wrong with the established connection and the Leader
decides a new one is necessary, the Leader will send a ``reconnect``
message. This might happen while connections are still being
established, or while the Follower thinks it still has a viable
connection (the Leader might observe problems that the Follower does
not), or after the Follower thinks the connection has been lost. In all
cases, the Leader is the only side which should send ``reconnect``. The
state machine code looks the same on both sides, for simplicity, but one
path on each side is never used.

Upon receiving a ``reconnect``, the Follower should stop any pending
connection attempts and terminate any existing connections (even if they
appear viable). Listening sockets may be retained, but any previous
connection made through them must be dropped.

Once all connections have stopped, the Follower should send a
``reconnecting`` message, then start the connection process for the next
generation, which will send new ``connection-hint`` messages for all
listening sockets.

Generations are non-overlapping. The Leader will drop all connections
from generation 1 before sending the ``reconnect`` for generation 2, and
will not initiate any gen-2 connections until it receives the matching
``reconnecting`` from the Follower. The Follower must drop all gen-1
connections before it sends the ``reconnecting`` response (even if it
thinks they are still functioning: if the Leader thought the gen-1
connection still worked, it wouldn’t have started gen-2).

(TODO: what about a follower->leader connection that was started before
start-dilation is received, and gets established on the Leader side
after start-dilation is sent? the follower will drop it after it
receives start-dilation, but meanwhile the leader may accept it as gen2)

(probably need to include the generation number in the handshake, or in
the derived key)

(TODO: reduce the number of round-trip stalls here, I’ve added too many)

Each side is in the “connecting” state (which encompasses both making
connection attempts and having an established connection) starting with
the receipt of a ``please-dilate`` message and a local ``w.dilate()``
call. The Leader remains in that state until it abandons the connection
and sends a ``reconnect`` message, at which point it remains in the
“flushing” state until the Follower’s ``reconnecting`` message is
received. The Follower remains in “connecting” until it receives
``reconnect``, then it stays in “dropping” until it finishes halting all
outstanding connections, after which it sends ``reconnecting`` and
switches back to “connecting”.

“Connection hints” are type/address/port records that tell the other
side of likely targets for L2 connections. Both sides will try to
determine their external IP addresses, listen on a TCP port, and
advertise ``(tcp, external-IP, port)`` as a connection hint. The Transit
Relay is also used as a (lower-priority) hint. These are sent in
``connection-hint`` records, which can be sent any time after both
sending and receiving a ``please`` record. Each side will initiate
connections upon receipt of the hints.

::

   { "type": "connection-hints",
     "hints": [ ... ]
   }

Hints can arrive at any time. One side might immediately send hints that
can be computed quickly, then send additional hints later as they become
available. For example, it might enumerate the local network interfaces
and send hints for all of the LAN addresses first, then send
port-forwarding (UPnP) requests to the local router. When the forwarding
is established (providing an externally-visible IP address and port), it
can send additional hints for that new endpoint. If the other peer
happens to be on the same LAN, the local connection can be established
without waiting for the router’s response.

Connection Hint Format
~~~~~~~~~~~~~~~~~~~~~~

Each member of the ``hints`` field describes a potential L2 connection
target endpoint, with an associated priority and a set of hints.

The priority is a number (positive or negative float), where larger
numbers indicate that the client supplying that hint would prefer to use
this connection over others of lower number. This indicates a sense of
cost or performance. For example, the Transit Relay is lower priority
than a direct TCP connection, because it incurs a bandwidth cost (on the
relay operator), as well as adding latency.

Each endpoint has a set of hints, because the same target might be
reachable by multiple hints. Once one hint succeeds, there is no point
in using the other hints.

TODO: think this through some more. What’s the example of a single
endpoint reachable by multiple hints? Should each hint have its own
priority, or just each endpoint?


L2 protocol
-----------

Upon successful connection (``connectionMade()`` in Twisted), both sides
send their handshake message. The Leader sends the ASCII bytes
``"Magic-Wormhole Dilation Handshake v1 Leader\n\n"``. The Follower
sends the ASCII bytes
``"Magic-Wormhole Dilation Handshake v1 Follower\n\n"``. This should
trigger an immediate error for most non-magic-wormhole listeners
(e.g. HTTP servers that were contacted by accident). If the wrong
handshake is received, the connection must be dropped. For debugging
purposes, the node might want to keep looking at data beyond the first
incorrect character and log a few hundred characters until the first
newline.

Everything beyond the last byte of the handshake consists of Noise
protocol messages.

L2 Message Framing
~~~~~~~~~~~~~~~~~~

Noise itself has a 65535-byte (``2**16 - 1``) limit on encoded message
sizes – however the *payload* is 16 bytes smaller that this limit. The
L2 protocol can deliver any *encoded* message up to an unsigned 4-byte
integer in length (4.0 GiB or ``2**32`` bytes). Due to overhead, the
actual limit for the payload of each frame is 4293918703 bytes (65537
Noise messages with 65519 bytes of payload each).

The encoding works like this: there is a 4-byte big-endian length field,
followed by some number of Noise packets. There is no leading length
field on each Noise packet: implementations MUST respect the Noise
limits. So if the length field indicates a message bigger than 65535,
the reader pulls 65535 bytes out of the stream, decrypts that blob as a
Noise message, subtracts 65535 from the total and continues. The last
Noise message will obviously be less than or exactly 65535 bytes.

The entire decoded blob is then “one L2 message” and is delivered
upstream.

On the encoding side, note that 16 bytes of each maximum 65535-byte
Noise message is used for authentication data. This means that when
encoding *payload*, implementations pull at most 65519 bytes of
plaintext at once and encrypt it (yielding 65535 bytes of ciphertext).
Implementations should avoid sending enormous messages like this, but it
is possible.

The Noise cryptography uses the ``NNpsk0`` pattern with the Leader as
the first party (``"-> psk, e"`` in the Noise spec), and the Follower as
the second (``"<- e, ee"``). The pre-shared-key is the “Dilation key”,
which is statically derived from the master PAKE key using HKDF. Each L2
connection uses the same Dilation key, but different ephemeral keys, so
each gets a different session key.

The exact Noise protocol in use is
``"Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s"``.

The HKDF used to derive the “Dilation key” is the RFC5869 HMAC
construction, with: shared-key-material consisting of the PAKE key; a
tag of the ASCII bytes ``"dilation-v1"``; no salt; and length equal to
32 bytes. The hash algorithm is SHA256. (The exact HKDF derivation is in
``wormhole/util.py``, wrapping an underlying ``cryptography`` library
primitive).

The Leader sends the first message, which is a psk-encrypted ephemeral
key. The Follower sends the next message, its own psk-encrypted
ephemeral key. These two messages are known as “handshake messages” in
the Noise protocol, and must be processed in a specific order (the
Leader must not accept the Follower’s message until it has generated its
own). Noise allows handshake messages to include a payload, but we do
not use this feature.

All subsequent messages are known as “Noise transport messages”, and use
independent channels for each direction, so they no longer have ordering
dependencies. Transport messages are encrypted by the shared key, in a
form that evolves as more messages are sent.

The Follower’s first transport message is an empty packet, which we use
as a “key confirmation message” (KCM).

The Leader doesn’t send a transport message right away: it waits to see
the Follower’s KCM, which indicates this connection is viable (i.e. the
Follower used the same Dilation key as the Leader, which means they both
used the same wormhole code).

The Leader delivers the now-viable protocol object to the L3 manager,
which will decide which connection to select. When some L2 connection is
selected to be the new L3, the Leader finally sends an empty KCM of its
own over that L2, to let the Follower know which connection has been
selected. All other L2 connections (either viable or still in handshake)
are dropped, and all other connection attempts are cancelled. All
listening sockets may or may not be shut down (TODO: think about it).

After sending their KCM, the Follower will wait for either an empty KCM
(at which point the L2 connection is delivered to the Dilation manager
as the new L3), a disconnection, or an invalid message (which causes the
connection to be dropped). Other connections and/or listening sockets
are stopped.

L2 Message Payload Encoding
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Above, we talk about *frames*. Inside each frame is a plaintext payload
(of maximum 4293918703 bytes as above). These plaintexts are
binary-encoded messages of the L2 protocol layer, consisting of these
types with corresponding 1-byte tags:

-  KCM: ``0x00``
-  PING: ``0x01``
-  PONG: ``0x02``
-  OPEN: ``0x03``
-  DATA: ``0x04``
-  CLOSE: ``0x05``
-  ACK: ``0x06``

Every message starts with its tag. Following the tag is a
message-specific encoding. In all messages, a “subchannel-id” (if
present) is a 4-byte big-endian unsigned int. A “sequence-number” (if
present) is a 4-byte big-endian unsigned int.

The messages are encoded like this (after the tag):

-  KCM: no other data
-  PING: arbitrary 4 byte “ping id”
-  PONG: arbitrary 4 byte “ping id”
-  OPEN: subchannel-id, sequence-number
-  DATA: subchannel-id, sequence-number, data
-  CLOSE: subchannel-id, sequence-number
-  ACK: sequence-number

For example, an OPEN would be encoded in 9 bytes of payload – so the
resulting Noise message is 9 + 16 bytes, surrounded by a frame with
leading 4-byte size for 29 bytes. A DATA message is thus 9 bytes plus
the actual “data payload” (when wrapped in Noise, and following the
limits in the framing section, this means the absolute biggest single
application message possible is 4293918703 - 9 or 4293918694 bytes).

Python Implementation Details
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For developers attempting to understand the Python reference
implementation (in the ``wormhole._dilation`` package):

Internally, the overall endeavour is managed by the ``Manager`` object.
For each generation, a single ``Connection`` object is created; this
object manages the race between potential hints-based peer connections.
A ``DilatedConnctionProtocol`` instance manages the Noise session
itself.

It knows via its ``_role`` attribute whether it is on the Leader or
Follower side, which affects both the role it plays in the Noise
pattern, and the reaction to receiving the handshake message / ephemeral
key (for which only the Follower sends an empty KCM message).

After that, the ``DilatedConnectionProtocol`` notifies the management
objects in three situations:

-  the Noise session produces a valid KCM message (``Connector``
   notified with ``add_candidate()``).
-  the Noise session reports a failed decryption (``Manager`` notified
   via ``connector_connection_lost()``)
-  the TCP session is lost (``Manager`` notified via
   ``connector_connection_lost()``)

During “normal operation” (after handshakes and KCMs), the ``Manager``
is notified on every received and decrypted message (via
``got_record``).

The L3 management object uses this reference to either close the
connection (for errors or when the selection process chooses someone
else), to send the KCM message (after selection, only for the Leader),
or to send other L4 messages. The L3 object will retain a reference to
the winning L2 object. See also the state-machine diagrams.


L3 protocol
-----------

The L3 layer is responsible for connection selection,
monitoring/keepalives, and message (de)serialization. Framing is handled
by L2, so the inbound L3 codepath receives single-message byte-strings,
and delivers the same down to L2 for encryption, framing, and
transmission.

Connection selection takes place exclusively on the Leader side, and
includes the following:

-  receipt of viable L2 connections from below (indicated by the first
   valid decrypted frame received for any given connection)
-  expiration of a timer
-  comparison of TBD quality/desirability/cost metrics of viable
   connections
-  selection of winner
-  instructions to losing connections to disconnect
-  delivery of KCM message through winning connection
-  retain reference to winning connection

On the Follower side, the L3 manager just waits for the first connection
to receive the Leader’s KCM, at which point it is retained and all
others are dropped.

The L3 manager knows which “generation” of connection is being
established. Each generation uses a different Dilation key (?), and is
triggered by a new set of L1 messages. Connections from one generation
should not be confused with those of a different generation.

Each time a new L3 connection is established, the L4 protocol is
notified. It will will immediately send all the L4 messages waiting in
its outbound queue. The L3 protocol simply wraps these in Noise frames
and sends them to the other side.

The L3 manager monitors the viability of the current connection, and
declares it as lost when bidirectional traffic cannot be maintained. It
uses PING and PONG messages to detect this. These also serve to keep NAT
entries alive, since many firewalls will stop forwarding packets if they
don’t observe any traffic for e.g. 5 minutes.

Our goals are:

-  don’t allow more than 30? seconds to pass without at least *some*
   data being sent along each side of the connection
-  allow the Leader to detect silent connection loss within 60? seconds
-  minimize overhead

We need both sides to:

-  maintain a 30-second repeating timer
-  set a flag each time we write to the connection
-  each time the timer fires, if the flag was clear then send a PONG,
   otherwise clear the flag

In addition, the Leader must:

-  run a 60-second repeating timer (ideally somewhat offset from the
   other)
-  set a flag each time we receive data from the connection
-  each time the timer fires, if the flag was clear then drop the
   connection, otherwise clear the flag

In the future, we might have L2 links that are less connection-oriented,
which might have a unidirectional failure mode, at which point we’ll
need to monitor full round-trips. To accomplish this, the Leader will
send periodic unconditional PINGs, and the Follower will respond with
PONGs. If the Leader->Follower connection is down, the PINGs won’t
arrive and no PONGs will be produced. If the Follower->Leader direction
has failed, the PONGs won’t arrive. The delivery of both will be delayed
by actual data, so the timeouts should be adjusted if we see regular
data arriving.

If the connection is dropped before the wormhole is closed (either the
other end explicitly dropped it, we noticed a problem and told TCP to
drop it, or TCP noticed a problem itself), the Leader-side L3 manager
will initiate a reconnection attempt. This uses L1 to send a new DILATE
message through the mailbox server, along with new connection hints.
Eventually this will result in a new L3 connection being established.

Finally, L3 is responsible for message serialization and
deserialization. L2 performs decryption and delivers plaintext frames to
L3. Each frame starts with a one-byte type indicator. The rest of the
message depends upon the type:

-  0x00 PING, 4-byte ping-id
-  0x01 PONG, 4-byte ping-id
-  0x02 OPEN, 4-byte subchannel-id, 4-byte seqnum
-  0x03 DATA, 4-byte subchannel-id, 4-byte seqnum, variable-length
   payload
-  0x04 CLOSE, 4-byte subchannel-id, 4-byte seqnum
-  0x05 ACK, 4-byte response-seqnum

All seqnums are big-endian, and are provided by the L4 protocol. The
other fields are arbitrary and not interpreted as integers. The
subchannel-ids must be allocated by both sides without collision, but
otherwise they are only used to look up L5 objects for dispatch. The
response-seqnum is always copied from the OPEN/DATA/CLOSE packet being
acknowledged.

L3 consumes the PING and PONG messages. Receiving any PING will provoke
a PONG in response, with a copy of the ping-id field. The 30-second
timer will produce unprovoked PONGs with a ping-id of all zeros. A
future viability protocol will use PINGs to test for roundtrip
functionality.

All other messages (OPEN/DATA/CLOSE/ACK) are deserialized and delivered
“upstairs” to the L4 protocol handler.

The current L3 connection’s ``IProducer``/``IConsumer`` interface is
made available to the L4 flow-control manager.

L4 protocol
-----------

The L4 protocol manages a durable stream of OPEN/DATA/CLOSE/ACK
messages. Since each will be enclosed in a Noise frame before they pass
to L3, they do not need length fields or other framing.

Each OPEN/DATA/CLOSE has a sequence number, starting at 0, and
monotonically increasing by 1 for each message. Each direction has a
separate number space.

The L4 manager maintains a double-ended queue of unacknowledged outbound
messages. Subchannel activity (opening, closing, sending data) cause
messages to be added to this queue. If an L3 connection is available,
these messages are also sent over that connection, but they remain in
the queue in case the connection is lost and they must be retransmitted
on some future replacement connection. Messages stay in the queue until
they can be retired by the receipt of an ACK with a matching
response-sequence-number. This provides reliable message delivery that
survives the L3 connection being replaced.

ACKs are not acked, nor do they have seqnums of their own. Each inbound
side remembers the highest ACK it has sent, and ignores incoming
OPEN/DATA/CLOSE messages with that sequence number or higher. This
ensures in-order at-most-once processing of OPEN/DATA/CLOSE messages.

Each inbound OPEN message causes a new L5 subchannel object to be
created. Subsequent DATA/CLOSE messages for the same subchannel-id are
delivered to that object.

Each time an L3 connection is established, the side will immediately
send all L4 messages waiting in the outbound queue. A future protocol
might reduce this duplication by including the highest received
sequence number in the L1 PLEASE message, which would effectively
retire queued messages before initiating the L2 connection process. On
any given L3 connection, all messages are sent in-order. The receipt
of an ACK for seqnum ``N`` allows all messages with ``seqnum <= N`` to
be retired.

The L4 layer is also responsible for managing flow control among the L3
connection and the various L5 subchannels.

L5 subchannels
--------------

The L5 layer consists of a collection of “subchannel” objects, a
dispatcher, and the endpoints that provide the Twisted-flavored API.

Other than the “control channel”, all subchannels are created by a
client endpoint connection API. The side that calls this API is named
the Initiator, and the other side is named the Acceptor. Subchannels can
be initiated in either direction, independent of the Leader/Follower
distinction. For a typical file-transfer application, the subchannel
would be initiated by the side seeking to send a file.

Each subchannel uses a distinct subchannel-id, which is a four-byte
identifier. Both directions share a number space (unlike L4 seqnums), so
the rule is that the Leader side sets the last bit of the last byte to a
1, while the Follower sets it to a 0. These are not generally treated as
integers, however for the sake of debugging, the implementation
generates them with a simple big-endian-encoded counter (``counter*2+1``
for the Leader, ``counter*2+2`` for the Follower, with id ``0`` reserved
for the control channel).

When the ``client_ep.connect()`` API is called, the Initiator allocates
a subchannel-id and sends an OPEN. It can then immediately send DATA
messages with the outbound data (there is no special response to an
OPEN, so there is no need to wait). The Acceptor will trigger their
``.connectionMade`` handler upon receipt of the OPEN.

Subchannels are durable: they do not close until one side calls
``.loseConnection`` on the subchannel object (or the enclosing Wormhole
is closed). Either the Initiator or the Acceptor can call
``.loseConnection``. This causes a CLOSE message to be sent (with the
subchannel-id). The other side will send its own CLOSE message in
response. Each side will signal the ``.connectionLost()`` event upon
receipt of a CLOSE.

There is no equivalent to TCP’s “half-closed” state, however if only one
side calls ``close()``, then all data written before that call will be
delivered before the other side observes ``.connectionLost()``. Any
inbound data that was queued for delivery before the other side sees the
CLOSE will still be delivered to the side that called ``close()`` before
it sees ``.connectionLost()``. Internally, the side which called
``.loseConnection`` will remain in a special “closing” state until the
CLOSE response arrives, during which time DATA payloads are still
delivered. After calling ``close()`` (or receiving CLOSE), any outbound
``.write()`` calls will trigger an error.

(TODO: it would be nice to have half-close, especially for simple
FTP-like file transfers)

DATA payloads that arrive for a non-open subchannel are logged and
discarded.

This protocol calls for one OPEN and two CLOSE messages for each
subchannel, with some arbitrary number of DATA messages in between.
Subchannel-ids should not be reused (it would probably work, the
protocol hasn’t been analyzed enough to be sure).

The “control channel” is special. It uses a subchannel-id of all zeros,
and is opened implicitly by both sides as soon as the first L3
connection is selected. It is routed to a special client-on-both-sides
endpoint, rather than causing the listening endpoint to accept a new
connection. This avoids the need for application-level code to negotiate
who should be the one to open it. The Leader/Follower distinction is
private to the Wormhole internals: applications are not obligated to
pick a side. Applications which need to negotiate their way into
asymmetry should send a random number through the control channel and
use it to assign themselves an application-level role.

OPEN and CLOSE messages for the control channel are logged and
discarded. The control-channel client endpoints can only be used once,
and does not close until the Wormhole itself is closed.

Each OPEN/DATA/CLOSE message is delivered to the L4 object for queueing,
delivery, and eventual retirement. The L5 layer does not keep track of
old messages.

Flow Control
~~~~~~~~~~~~

Subchannels are flow-controlled by pausing their writes when the L3
connection is paused, and pausing the L3 connection when the subchannel
signals a pause. When the outbound L3 connection is full, *all*
subchannels are paused. Likewise the inbound connection is paused if
*any* of the subchannels asks for a pause. This is much easier to
implement and improves our utilization factor (we can use TCP’s
window-filling algorithm, instead of rolling our own), but will block
all subchannels even if only one of them gets full. This shouldn’t
matter for many applications, but might be noticeable when combining
very different kinds of traffic (e.g. a chat conversation sharing a
wormhole with file-transfer might prefer the IM text to take priority).

(TODO: it would be nice to have per-subchannel flow control)

Each subchannel implements Twisted’s ``ITransport``, ``IProducer``, and
``IConsumer`` interfaces. The Endpoint API causes a new ``IProtocol``
object to be created (by the caller’s factory) and glued to the
subchannel object in the ``.transport`` property, as is standard in
Twisted-based applications.

All subchannels are also paused when the L3 connection is lost, and are
unpaused when a new replacement connection is selected.
