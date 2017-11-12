# Protocol/API/Library Introduction

The magic-wormhole (Python) distribution provides several things: an
executable tool ("bin/wormhole"), an importable library (`import wormhole`),
the URL of a publically-available Rendezvous Server, and the definition of a
protocol used by all three.

The executable tool provides basic sending and receiving of files,
directories, and short text strings. These all use `wormhole send` and
`wormhole receive` (which can be abbreviated as `wormhole tx` and `wormhole
rx`). It also has a mode to facilitate the transfer of SSH keys. This tool,
while useful on its own, is just one possible use of the protocol.

The `wormhole` library provides an API to establish a bidirectional ordered
encrypted record pipe to another instance (where each record is an
arbitrary-sized bytestring). This does not provide file-transfer directly:
the "bin/wormhole" tool speaks a simple protocol through this record pipe to
negotiate and perform the file transfer.

`wormhole/cli/public_relay.py` contains the URLs of a Rendezvous Server and a
Transit Relay which I provide to support the file-transfer tools, which other
developers should feel free to use for their applications as well. I cannot
make any guarantees about performance or uptime for these servers: if you
want to use Magic Wormhole in a production environment, please consider
running a server on your own infrastructure (just run `wormhole-server start`
and modify the URLs in your application to point at it).

## The Magic-Wormhole Protocol

There are several layers to the protocol.

At the bottom level, each client opens a WebSocket to the Rendezvous Server,
sending JSON-based commands to the server, and receiving similarly-encoded
messages. Some of these commands are addressed to the server itself, while
others are instructions to queue a message to other clients, or are
indications of messages coming from other clients. All these messages are
described in "server-protocol.md".

These inter-client messages are used to convey the PAKE protocol exchange,
then a "VERSION" message (which doubles to verify the session key), then some
number of encrypted application-level data messages. "client-protocol.md"
describes these wormhole-to-wormhole messages.

Each wormhole-using application is then free to interpret the data messages
as it pleases. The file-transfer app sends an "offer" from the `wormhole
send` side, to which the `wormhole receive` side sends a response, after
which the Transit connection is negotiated (if necessary), and finally the
data is sent through the Transit connection. "file-transfer-protocol.md"
describes this application's use of the client messages.

## The `wormhole` API

Application use the `wormhole` library to establish wormhole connections and
exchange data through them. Please see `api.md` for a complete description of
this interface.

