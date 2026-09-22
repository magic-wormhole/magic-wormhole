# Key Setup in magic-wormhole (python)

Every wormhole connection begins with the "Key Setup" stage. This is a
series of messages, exchanged with the peer, through the mailbox
server, which serve to negotiate a common protocol version, establish,
and then verify the shared encryption key. These protocols are defined
in the `magic-wormhole-protocols` repository, in the
`version-negotiation.md` document.

Clients are free to implement the specification in any compatible way
they want, but this document describes the approach used by this
specific (Python) implementation. It will be easier to follow if you
read that `version-negotiation.md` page first.

## Components

The `Encryption` instance manages all cryptgraphy for the main
(non-dilated) wormhole. Inbound messages from the `Mailbox` machine
arrive as `got_message()` input events, as do events from the `Boss`
like `got_code()` and `send()` Internally, there is an
`_EncryptionCore` state machine that does no IO: it merely returns
lists of actions that it wants `Encryption` to do on its behalf.

Internally, the `_EncryptionCore` has a `Negotiator` which manages one
or more key setup protocol machines. Each specific key-setup protocol
version is managed by a different class: `KeySetup_V0`, `KeySetup_V1`,
etc, all of which implement the `IKeySetup` interface.

The Negotiator receives four events: `got_code`, `ready`,
`got_versions`, and `got_key_setup_message`. The `ready` message
indicates that we should not wait any longer to send our initial
"PAKE-0" (`phase="pake"`): it exists to improve performance in the
case where this client runs second, and can learn the first client's
`side` and available versions before responding. `got_versions` is
called upon the receipt of the peer's PAKE-0, with the
`my_key_setup_versions` list. This allows the negotiated version to be
computed. And then `got_key_setup_message` is called with each PAKE-n
or VERSION message from the peer.

The Negotiator has a number of "waiting" states, where it is waiting
for enough information to proceed, or waiting to be told it should not
wait any longer. It then has three other states: Speculating,
Negotiating, and Done.

## Version Preferences

`negotiator.py` has a static list named `KEY_SETUP_VERSIONS` which
defines the client's preferred key-setup versions. This list is sorted
in *increasing* priority, so `["v0", "v1", "v2"]` means that the
client most wants to do v2, and will only use v0 as a last
resort. Branches which add experimental protocols should put them on
the end of the list, so they will be used in preference to the
standard ones. Note that only the Leaders preference order is
respected (the side with a lexicographically-higher `side` value will
be the Leader).

Experimental versions can use any unclaimed name, however for the sake
of decentralized coordination, any version not defined by the official
wormhole protocol specification docs should use a name that is scoped
to a domain name, like
`example.com/magic-wormhole/specification/v45-quasar`. Ideally the
name will be a functioning URL that hosts documentation about the
protocol in question. For brevity's sake, we treat the "standard"
"v0"/"v1"/etc versions as special, and everyone will know that their
specs are defined in the offical docs.

This list is sampled when a Negotiator instance is created, so unit
tests can `mock.patch` the list just during construction, to control
exactly which version is used.

## Speculating

If the Negotiator needs to begin without knowing a precise version
(the initiator must always do this), it will go into the Speculating
state, where it builds a panel of IKeySetup instances for every
version about which we are optimistic. Each member of this panel is
asked to `start_pake0()`, which must return a dict of properties to be
merged into the PAKE-0 message. Any overlapping properties must match
(ensured by using shared helpers like `SPAKE2_Helper`). The Negotiator
adds the `my_key_setup_versions` list and transmits the PAKE-0
message.

When the peer's PAKE-0 arrives, their own version list lets us resolve
the exact version to use. If this was part of the speculative panel,
we throw the others away and proceed with the winner. If it was not,
we must create a new one, and get its list of initial properties with
`start_pake1()`, which the Negotiator sends in a the PAKE-1
message. In either case, we move to the Negotiating state.

## Negotiating

We can jump directly to Negotiating if the peer's PAKE-0 arrives
first, before we try to make our own. This allows us to start on
exactly the right version, without any need to guess. In this case, a
single IKeySetup instance is created, and we call its `start_pake0()`
as before.

While in the Negotiating state, all inbound key-setup messages (PAKE-N
and VERSION) are delivered to the IKeySetup's `input()` method.

Both `input()` and `start_pake()` return two things: a list of
actions, and the `phase` of the next desired key-setup message. The
actions are `Send()` (to send an outbound key-setup message),
`HaveAllegedKey()` (to signal that a key is known, but not yet
verified), or `Done()` (to signal that the key is verified, and
deliver the application/wormhole version data).

The `phase` output is how the Negotiator knows which inbound message
to deliver. It will count upwards from PAKE-0 (`phase="pake"`),
PAKE-1, PAKE-2, and then eventually VERSION (`phase="version"`), and
then finally None to indicate that no more messages are expected.

## Transcript Management

Good protocols will have the negotiation transcript into their session
key, to protect against version-downgrade attacks. The "v0" protocol
did not do this, but v1/v2 do.

To support this, the winning IKeySetup must be told about all the
messages we have sent. The inbound messages all arrive at `input()`,
and should be appended to a list for hashing later.

The later outbound messages are created by the IKeySetup instance (and
signed with `Send()`), and the instance is responsible for collecting
those too.

However the initial PAKE-0 is created by merging components from one
or more IKeySetup instances, and adding the `my_key_setup_versions`
list. The IKeySetup contributes to this, but does not use a `Send()`
action for it. Instead, the Negotiator will invoke
`submit_outbound_pake0()` on the winning instance, to inform it of the
contents of that PAKE-0. If the IKeySetup was created late, the PAKE-0
message will besubmitted as an argument to `start_pake1()`. In either
case, the IKeySetup instance must add it to the transcript for hashing
later.

The transcript must be sorted and serialized, to make sure that both
sides get the same contents despite messages arriving in different
orders. A helper module named `hash_transcript.py` is provided for
this purpose, but the exact details are a property of the key-setup
specification document.

All the modern protocols (v1/v2) emit a "pre-version" message in their
last PAKE-N phase, followed by the encrypted VERSION message for use
as a key-confirmation message. These two messages do *not* go into the
transcript: the key is finalized before they are created.

## Done

When the IKeySetup has received confirmation (generally by
successfully decrypting and verifying the MAC on the key-confirmation
`phase="version"` message), it emits a `Done()` action, which includes
the final session key and the version-data plaintext bytes. The
Negotiator returns this data to the `_EncryptionCore` and deletes the
IKeySetup. `_EncryptionCore` informs the Boss and then switches to
decryption mode: it encrypts and sends any pending outbound messages,
and it decrypts and drains the backlog of inbound messages.


# Backwards Compatibility

All (python) releases from the first (0.2.0, in 2015) up to the most
recent (0.24.0, in 2026) spoke the same protocol, which uses only
SPAKE2. This protocol has been retroactively named "v0".

All known implementations are tolerant of additional properties
appearing in the PAKE-0 (`phase="pake"`) message, but will crash or
halt if they receive unexpected phases. So the new
`my_key_setup_versions` property must be delivered in PAKE-0: it will
be ignored by older clients, but can signal a version upgrade in the
newer ones. Clients may not emit other phases (like PAKE-1) until they
are sure the peer is capable of accepting them.
