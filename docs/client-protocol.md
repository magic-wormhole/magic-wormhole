# Client-to-Client Protocol

Wormhole clients do not talk directly to each other (at least at first): they
only connect directly to the Rendezvous Server. They ask this server to
convey messages to the other client (via the `add` command and the `message`
response). This document explains the format of these client-to-client
messages.

Each such message contains a "phase" string, and a hex-encoded binary "body".

Any phase which is purely numeric (`^\d+$`) is reserved for application data,
and will be delivered in numeric order. All other phases are reserved for the
Wormhole client itself. Clients will ignore any phase they do not recognize.

Immediately upon opening the mailbox, clients send the `pake` phase, which
contains the binary SPAKE2 message (the one computed as `X+M*pw` or
`Y+N*pw`).

Upon receiving their peer's `pake` phase, clients compute and remember the
shared key. Then they send the encrypted `version` phase, whose plaintext
payload is a UTF-8-encoded JSON-encoded dictionary of metadata. This allows
the two Wormhole instances to signal their ability to do other things (like
"dilate" the wormhole). The version data will also include an `app_versions`
key which contains a dictionary of metadata provided by the application,
allowing apps to perform similar negotiation.

Both `version` and all numeric (app-specific) phases are encrypted. The
message body will be the hex-encoded output of a NACL SecretBox, keyed by a
phase+side -specific key (computed with HKDF-SHA256, using the shared PAKE
key as the secret input, and `wormhole:phase:%s%s % (SHA256(side),
SHA256(phase))` as the CTXinfo), with a random nonce.


