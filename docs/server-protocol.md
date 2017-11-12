# Rendezvous Server Protocol

## Concepts

The Rendezvous Server provides queued delivery of binary messages from one
client to a second, and vice versa. Each message contains a "phase" (a
string) and a body (bytestring). These messages are queued in a "Mailbox"
until the other side connects and retrieves them, but are delivered
immediately if both sides are connected to the server at the same time.

Mailboxes are identified by a large random string. "Nameplates", in contrast,
have short numeric identities: in a wormhole code like "4-purple-sausages",
the "4" is the nameplate.

Each client has a randomly-generated "side", a short hex string, used to
differentiate between echoes of a client's own message, and real messages
from the other client.

## Application IDs

The server isolates each application from the others. Each client provides an
"App Id" when it first connects (via the "BIND" message), and all subsequent
commands are scoped to this application. This means that nameplates
(described below) and mailboxes can be re-used between different apps. The
AppID is a unicode string. Both sides of the wormhole must use the same
AppID, of course, or they'll never see each other. The server keeps track of
which applications are in use for maintenance purposes.

Each application should use a unique AppID. Developers are encouraged to use
"DNSNAME/APPNAME" to obtain a unique one: e.g. the `bin/wormhole`
file-transfer tool uses `lothar.com/wormhole/text-or-file-xfer`.

## WebSocket Transport

At the lowest level, each client establishes (and maintains) a WebSocket
connection to the Rendezvous Server. If the connection is lost (which could
happen because the server was rebooted for maintenance, or because the
client's network connection migrated from one network to another, or because
the resident network gremlins decided to mess with you today), clients should
reconnect after waiting a random (and exponentially-growing) delay. The
Python implementation waits about 1 second after the first connection loss,
growing by 50% each time, capped at 1 minute.

Each message to the server is a dictionary, with at least a `type` key, and
other keys that depend upon the particular message type. Messages from server
to client follow the same format.

`misc/dump-timing.py` is a debug tool which renders timing data gathered from
the server and both clients, to identify protocol slowdowns and guide
optimization efforts. To support this, the client/server messages include
additional keys. Client->Server messages include a random `id` key, which is
copied into the `ack` that is immediately sent back to the client for all
commands (logged for the timing tool but otherwise ignored). Some
client->server messages (`list`, `allocate`, `claim`, `release`, `close`,
`ping`) provoke a direct response by the server: for these, `id` is copied
into the response. This helps the tool correlate the command and response.
All server->client messages have a `server_tx` timestamp (seconds since
epoch, as a float), which records when the message left the server. Direct
responses include a `server_rx` timestamp, to record when the client's
command was received. The tool combines these with local timestamps (recorded
by the client and not shared with the server) to build a full picture of
network delays and round-trip times.

All messages are serialized as JSON, encoded to UTF-8, and the resulting
bytes sent as a single "binary-mode" WebSocket payload.

Servers can signal `error` for any message type it does not recognize.
Clients and Servers must ignore unrecognized keys in otherwise-recognized
messages. Clients must ignore unrecognized message types from the Server.

## Connection-Specific (Client-to-Server) Messages

The first thing each client sends to the server, immediately after the
WebSocket connection is established, is a `bind` message. This specifies the
AppID and side (in keys `appid` and `side`, respectively) that all subsequent
messages will be scoped to. While technically each message could be
independent (with its own `appid` and `side`), I thought it would be less
confusing to use exactly one WebSocket per logical wormhole connection.

The first thing the server sends to each client is the `welcome` message.
This is intended to deliver important status information to the client that
might influence its operation. The Python client currently reacts to the
following keys (and ignores all others):

* `current_cli_version`: prompts the user to upgrade if the server's
  advertised version is greater than the client's version (as derived from
  the git tag)
* `motd`: prints this message, if present; intended to inform users about
  performance problems, scheduled downtime, or to beg for donations to keep
  the server running
* `error`: causes the client to print the message and then terminate. If a
  future version of the protocol requires a rate-limiting CAPTCHA ticket or
  other authorization record, the server can send `error` (explaining the
  requirement) if it does not see this ticket arrive before the `bind`.

A `ping` will provoke a `pong`: these are only used by unit tests for
synchronization purposes (to detect when a batch of messages have been fully
processed by the server). NAT-binding refresh messages are handled by the
WebSocket layer (by asking Autobahn to send a keepalive messages every 60
seconds), and do not use `ping`.

If any client->server command is invalid (e.g. it lacks a necessary key, or
was sent in the wrong order), an `error` response will be sent, This response
will include the error string in the `error` key, and a full copy of the
original message dictionary in `orig`.

## Nameplates

Wormhole codes look like `4-purple-sausages`, consisting of a number followed
by some random words. This number is called a "Nameplate".

On the Rendezvous Server, the Nameplate contains a pointer to a Mailbox.
Clients can "claim" a nameplate, and then later "release" it. Each claim is
for a specific side (so one client claiming the same nameplate multiple times
only counts as one claim). Nameplates are deleted once the last client has
released it, or after some period of inactivity.

Clients can either make up nameplates themselves, or (more commonly) ask the
server to allocate one for them. Allocating a nameplate automatically claims
it (to avoid a race condition), but for simplicity, clients send a claim for
all nameplates, even ones which they've allocated themselves.

Nameplates (on the server) must live until the second client has learned
about the associated mailbox, after which point they can be reused by other
clients. So if two clients connect quickly, but then maintain a long-lived
wormhole connection, the do not need to consume the limited space of short
nameplates for that whole time.

The `allocate` command allocates a nameplate (the server returns one that is
as short as possible), and the `allocated` response provides the answer.
Clients can also send a `list` command to get back a `nameplates` response
with all allocated nameplates for the bound AppID: this helps the code-input
tab-completion feature know which prefixes to offer. The `nameplates`
response returns a list of dictionaries, one per claimed nameplate, with at
least an `id` key in each one (with the nameplate string). Future versions
may record additional attributes in the nameplate records, specifically a
wordlist identifier and a code length (again to help with code-completion on
the receiver).

## Mailboxes

The server provides a single "Mailbox" to each pair of connecting Wormhole
clients. This holds an unordered set of messages, delivered immediately to
connected clients, and queued for delivery to clients which connect later.
Messages from both clients are merged together: clients use the included
`side` identifier to distinguish echoes of their own messages from those
coming from the other client.

Each mailbox is "opened" by some number of clients at a time, until all
clients have closed it. Mailboxes are kept alive by either an open client, or
a Nameplate which points to the mailbox (so when a Nameplate is deleted from
inactivity, the corresponding Mailbox will be too).

The `open` command both marks the mailbox as being opened by the bound side,
and also adds the WebSocket as subscribed to that mailbox, so new messages
are delivered immediately to the connected client. There is no explicit ack
to the `open` command, but since all clients add a message to the mailbox as
soon as they connect, there will always be a `message` reponse shortly after
the `open` goes through. The `close` command provokes a `closed` response.

The `close` command accepts an optional "mood" string: this allows clients to
tell the server (in general terms) about their experiences with the wormhole
interaction. The server records the mood in its "usage" record, so the server
operator can get a sense of how many connections are succeeding and failing.
The moods currently recognized by the Rendezvous Server are:

* `happy` (default): the PAKE key-establishment worked, and the client saw at
  least one valid encrypted message from its peer
* `lonely`: the client gave up without hearing anything from its peer
* `scary`: the client saw an invalid encrypted message from its peer,
  indicating that either the wormhole code was typed in wrong, or an attacker
  tried (and failed) to guess the code
* `errory`: the client encountered some other error: protocol problem or
  internal error

The server will also record `pruney` if it deleted the mailbox due to
inactivity, or `crowded` if more than two sides tried to access the mailbox.

When clients use the `add` command to add a client-to-client message, they
will put the body (a bytestring) into the command as a hex-encoded string in
the `body` key. They will also put the message's "phase", as a string, into
the `phase` key. See client-protocol.md for details about how different
phases are used.

When a client sends `open`, it will get back a `message` response for every
message in the mailbox. It will also get a real-time `message` for every
`add` performed by clients later. These `message` responses include "side"
and "phase" from the sending client, and "body" (as a hex string, encoding
the binary message body). The decoded "body" will either by a random-looking
cryptographic value (for the PAKE message), or a random-looking encrypted
blob (for the VERSION message, as well as all application-provided payloads).
The `message` response will also include `id`, copied from the `id` of the
`add` message (and used only by the timing-diagram tool).

The Rendezvous Server does not de-duplicate messages, nor does it retain
ordering: clients must do both if they need to.

## All Message Types

This lists all message types, along with the type-specific keys for each (if
any), and which ones provoke direct responses:

* S->C welcome {welcome:}
* (C->S) bind {appid:, side:}
* (C->S) list {} -> nameplates
* S->C nameplates {nameplates: [{id: str},..]}
* (C->S) allocate {} -> allocated
* S->C allocated {nameplate:}
* (C->S) claim {nameplate:} -> claimed
* S->C claimed {mailbox:}
* (C->S) release {nameplate:?} -> released
* S->C released
* (C->S) open {mailbox:}
* (C->S) add {phase: str, body: hex} -> message (to all connected clients)
* S->C message {side:, phase:, body:, id:}
* (C->S) close {mailbox:?, mood:?} -> closed
* S->C closed
* S->C ack
* (C->S) ping {ping: int} -> ping
* S->C pong {pong: int}
* S->C error {error: str, orig:}

## Persistence

The server stores all messages in a database, so it should not lose any
information when it is restarted. The server will not send a direct
response until any side-effects (such as the message being added to the
mailbox) have been safely committed to the database.

The client library knows how to resume the protocol after a reconnection
event, assuming the client process itself continues to run.

Clients which terminate entirely between messages (e.g. a secure chat
application, which requires multiple wormhole messages to exchange
address-book entries, and which must function even if the two apps are never
both running at the same time) can use "Journal Mode" to ensure forward
progress is made: see "journal.md" for details.
