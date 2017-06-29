# File-Transfer Protocol

The `bin/wormhole` tool uses a Wormhole to establish a connection, then
speaks a file-transfer -specific protocol over that Wormhole to decide how to
transfer the data. This application-layer protocol is described here.

All application-level messages are dictionaries, which are JSON-encoded and
and UTF-8 encoded before being handed to `wormhole.send` (which then encrypts
them before sending through the rendezvous server to the peer).

## Sender

`wormhole send` has two main modes: file/directory (which requires a
non-wormhole Transit connection), or text (which does not).

If the sender is doing files or directories, its first message contains just
a `transit` key, whose value is a dictionary with `abilities-v1` and
`hints-v1` keys. These are given to the Transit object, described below.

Then (for both files/directories and text) it sends a message with an `offer`
key. The offer contains a single key, exactly one of (`message`, `file`, or
`directory`). For `message`, the value is the message being sent. For `file`
and `directory`, it contains a dictionary with additional information:

* `message`: the text message, for text-mode
* `file`: for file-mode, a dict with `filename` and `filesize`
* `directory`: for directory-mode, a dict with:
 * `mode`: the compression mode, currently always `zipfile/deflated`
 * `dirname`
 * `zipsize`: integer, size of the transmitted data in bytes
 * `numbytes`: integer, estimated total size of the uncompressed directory
 * `numfiles`: integer, number of files+directories being sent

The sender runs a loop where it waits for similar dictionary-shaped messages
from the recipient, and processes them. It reacts to the following keys:

* `error`: use the value to throw a TransferError and terminates
* `transit`: use the value to build the Transit instance
* `answer`:
 * if `message_ack: ok` is in the value (we're in text-mode), then exit with success
 * if `file_ack: ok` in the value (and we're in file/directory mode), then
   wait for Transit to connect, then send the file through Transit, then wait
   for an ack (via Transit), then exit

The sender can handle all of these keys in the same message, or spaced out
over multiple ones. It will ignore any keys it doesn't recognize, and will
completely ignore messages that don't contain any recognized key. The only
constraint is that the message containing `message_ack` or `file_ack` is the
last one: it will stop looking for wormhole messages at that point.

## Recipient

`wormhole receive` is used for both file/directory-mode and text-mode: it
learns which is being used from the `offer` message.

The recipient enters a loop where it processes the following keys from each
received message:

* `error`: if present in any message, the recipient raises TransferError
(with the value) and exits immediately (before processing any other keys)
* `transit`: the value is used to build the Transit instance
* `offer`: parse the offer:
 * `message`: accept the message and terminate
 * `file`: connect a Transit instance, wait for it to deliver the indicated
  number of bytes, then write them to the target filename
 * `directory`: as with `file`, but unzip the bytes into the target directory

## Transit

The Wormhole API does not currently provide for large-volume data transfer
(this feature will be added to a future version, under the name "Dilated
Wormhole"). For now, bulk data is sent through a "Transit" object, which does
not use the Rendezvous Server. Instead, it tries to establish a direct TCP
connection from sender to recipient (or vice versa). If that fails, both
sides connect to a "Transit Relay", a very simple Server that just glues two
TCP sockets together when asked.

The Transit object is created with a key (the same key on each side), and all
data sent through it will be encrypted with a derivation of that key. The
transit key is also used to derive handshake messages which are used to make
sure we're talking to the right peer, and to help the Transit Relay match up
the two client connections. Unlike Wormhole objects (which are symmetric),
Transit objects come in pairs: one side is the Sender, and the other is the
Receiver.

Like Wormhole, Transit provides an encrypted record pipe. If you call
`.send()` with 40 bytes, the other end will see a `.gotData()` with exactly
40 bytes: no splitting, merging, dropping, or re-ordering. The Transit object
also functions as a twisted Producer/Consumer, so it can be connected
directly to file-readers and writers, and does flow-control properly.

Most of the complexity of the Transit object has to do with negotiating and
scheduling likely targets for the TCP connection.

Each Transit object has a set of "abilities". These are outbound connection
mechanisms that the client is capable of using. The basic CLI tool (running
on a normal computer) has two abilities: `direct-tcp-v1` and `relay-v1`.

* `direct-tcp-v1` indicates that it can make outbound TCP connections to a
  requested host and port number. "v1" means that the first thing sent over
  these connections is a specific derived handshake message, e.g. `transit
  sender HEXHEX ready\n\n`.
* `relay-v1` indicates it can connect to the Transit Relay and speak the
  matching protocol (in which the first message is `please relay HEXHEX for
  side HEX\n`, and the relay might eventually say `ok\n`).

Future implementations may have additional abilities, such as connecting
directly to Tor onion services, I2P services, WebSockets, WebRTC, or other
connection technologies. Implementations on some platforms (such as web
browsers) may lack `direct-tcp-v1` or `relay-v1`.

While it isn't strictly necessary for both sides to emit what they're capable
of using, it does help performance: a Tor Onion-service -capable receiver
shouldn't spend the time and energy to set up an onion service if the sender
can't use it.

After learning the abilities of its peer, the Transit object can create a
list of "hints", which are endpoints that the peer should try to connect to.
Each hint will fall under one of the abilities that the peer indicated it
could use. Hints have types like `direct-tcp-v1`, `tor-tcp-v1`, and
`relay-v1`. Hints are encoded into dictionaries (with a mandatory `type` key,
and other keys as necessary):

* `direct-tcp-v1` {hostname:, port:, priority:?}
* `tor-tcp-v1` {hostname:, port:, priority:?}
* `relay-v1` {hints: [{hostname:, port:, priority:?}, ..]}

For example, if our peer can use `direct-tcp-v1`, then our Transit object
will deduce our local IP addresses (unless forbidden, i.e. we're using Tor),
listen on a TCP port, then send a list of `direct-tcp-v1` hints pointing at
all of them. If our peer can use `relay-v1`, then we'll connect to our relay
server and give the peer a hint to the same.

`tor-tcp-v1` hints indicate an Onion service, which cannot be reached without
Tor. `direct-tcp-v1` hints can be reached with direct TCP connections (unless
forbidden) or by proxying through Tor. Onion services take about 30 seconds
to spin up, but bypass NAT, allowing two clients behind NAT boxes to connect
without a transit relay (really, the entire Tor network is acting as a
relay).

The file-transfer application uses `transit` messages to convey these
abilities and hints from one Transit object to the other. After updating the
Transit objects, it then asks the Transit object to connect, whereupon
Transit will try to connect to all the hints that it can, and will use the
first one that succeeds.

The file-transfer application, when actually sending file/directory data,
will close the Wormhole as soon as it has enough information to begin opening
the Transit connection. The final ack of the received data is sent through
the Transit object, as a UTF-8-encoded JSON-encoded dictionary with `ack: ok`
and `sha256: HEXHEX` containing the hash of the received data.


## Future Extensions

Transit will be extended to provide other connection techniques:

* WebSocket: usable by web browsers, not too hard to use by normal computers,
  requires direct (or relayed) TCP connection
* WebRTC: usable by web browsers, hard-but-technically-possible to use by
  normal computers, provides NAT hole-punching for "free"
* (web browsers cannot make direct TCP connections, so interop between
  browsers and CLI clients will either require adding WebSocket to CLI, or a
  relay that is capable of speaking/bridging both)
* I2P: like Tor, but not capable of proxying to normal TCP hints.
* ICE-mediated STUN/STUNT: NAT hole-punching, assisted somewhat by a server
  that can tell you your external IP address and port. Maybe implemented as a
  uTP stream (which is UDP based, and thus easier to get through NAT).

The file-transfer protocol will be extended too:

* "command mode": establish the connection, *then* figure out what we want to
  use it for, allowing multiple files to be exchanged, in either direction.
  This is to support a GUI that lets you open the wormhole, then drop files
  into it on either end.
* some Transit messages being sent early, so ports and Onion services can be
  spun up earlier, to reduce overall waiting time
* transit messages being sent in multiple phases: maybe the transit
  connection can progress while waiting for the user to confirm the transfer

The hope is that by sending everything in dictionaries and multiple messages,
there will be enough wiggle room to make these extensions in a
backwards-compatible way. For example, to add "command mode" while allowing
the fancy new (as yet unwritten) GUI client to interoperate with
old-fashioned one-file-only CLI clients, we need the GUI tool to send an "I'm
capable of command mode" in the VERSION message, and look for it in the
received VERSION. If it isn't present, it will either expect to see an offer
(if the other side is sending), or nothing (if it is waiting to receive), and
can explain the situation to the user accordingly. It might show a locked set
of bars over the wormhole graphic to mean "cannot send", or a "waiting to
send them a file" overlay for send-only.
