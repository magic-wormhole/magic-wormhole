# Client-to-Client Protocol

Wormhole clients do not talk directly to each other (at least at first): they
only connect directly to the Rendezvous Server. They ask this server to
convey messages to the other client (via the `add` command and the `message`
response). This document explains the format of these client-to-client
messages.

Each such message contains a "phase" string, and a hex-encoded binary "body".

Any phase which is purely numeric (`^\d+$`) is reserved for encrypted
application data. The Rendezvous server may deliver these messages multiple
times, or out-of-order, but the wormhole client will deliver the
corresponding decrypted data to the application in strict numeric order. All
other (non-numeric) phases are reserved for the Wormhole client itself.
Clients will ignore any phase they do not recognize.

Immediately upon opening the mailbox, clients send the `pake` phase, which
contains the binary SPAKE2 message (the one computed as `X+M*pw` or
`Y+N*pw`).

Upon receiving their peer's `pake` phase, clients compute and remember the
shared key. They derive the "verifier" (a hash of the shared key) and deliver
it to the application by calling `got_verifier`: applications can display
this to users who want additional assurance (by manually comparing the values
from both sides: they ought to be identical). At this point clients also send
the encrypted `version` phase, whose plaintext payload is a UTF-8-encoded
JSON-encoded dictionary of metadata. This allows the two Wormhole instances
to signal their ability to do other things (like "dilate" the wormhole). The
version data will also include an `app_versions` key which contains a
dictionary of metadata provided by the application, allowing apps to perform
similar negotiation.

At this stage, the client knows the supposed shared key, but has not yet seen
evidence that the peer knows it too. When the first peer message arrives
(i.e. the first message with a `.side` that does not equal our own), it will
be decrypted: we use authenticated encryption (`nacl.SecretBox`), so if this
decryption succeeds, then we're confident that *somebody* used the same
wormhole code as us. This event pushes the client mood from "lonely" to
"happy".

This might be triggered by the peer's `version` message, but if we had to
re-establish the Rendezvous Server connection, we might get peer messages out
of order and see some application-level message first.

When a `version` message is successfully decrypted, the application is
signaled with `got_version`. When any application message is successfully
decrypted, `received` is signaled. Application messages are delivered
strictly in-order: if we see phases 3 then 2 then 1, all three will be
delivered in sequence after phase 1 is received.

If any message cannot be successfully decrypted, the mood is set to "scary",
and the wormhole is closed. All pending Deferreds will be errbacked with a
`WrongPasswordError` (a subclass of `WormholeError`), the nameplate/mailbox
will be released, and the WebSocket connection will be dropped. If the
application calls `close()`, the resulting Deferred will not fire until
deallocation has finished and the WebSocket is closed, and then it will fire
with an errback.

Both `version` and all numeric (app-specific) phases are encrypted. The
message body will be the hex-encoded output of a NaCl `SecretBox`, keyed by a
phase+side -specific key (computed with HKDF-SHA256, using the shared PAKE
key as the secret input, and `wormhole:phase:%s%s % (SHA256(side),
SHA256(phase))` as the CTXinfo), with a random nonce.


