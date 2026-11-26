from attrs import frozen
from zope.interface import Interface

## actions, returned by IKeySetup.output()

# Send means a key-setup message needs to be sent to the peer
@frozen
class Send:
    side: str
    phase: str
    body: bytes

# Indicate that key setup has achieved a potential key and is waiting
# for the VERSION/key-confirmation-message to arrive so it can be
# verified
#
@frozen
class HaveAllegedKey:
    pass

# Done indicates the key has been verified and key-setup is
# complete. The action includes the session key and the decrypted
# VERSION message (as bytes). "Done" is terminal: no further actions
# will appear. The caller should derive the verifier string and notify
# Boss:
#  * boss.got_verifier(verifier)
#  * boss.got_versions(versions)
#
@frozen
class Done:
    key: bytes
    version_data: bytes

KeySetupAction = Send | HaveAllegedKey | Done
KeySetupActions = list[KeySetupAction]
NextKeySetupInput = tuple[KeySetupActions, str | None] # (actions, wanted)
MessageTuple = tuple[str, str, bytes] # (side, phase, body)

# instances are created with my_side: str

class IKeySetup(Interface):
    def start_pake0(code: str, their_side: str | None) -> dict:
        """Set the wormhole code and generate the PAKE-0 components.

        Call this when the complete wormhole code is available and
        we've either received the peer's PAKE-0 (phase="pake") message
        or we know we shouldn't wait for it. It will be used by any
        PAKE algorithms involved in this particular version of the key
        setup protocol. The return value contains components to go into
        our outbound PAKE-0 message.

        If 'their_side' is None, which happens when we decide to start
        before input() gets the peer's PAKE-0 message, start() may put
        speculative components in the outbound PAKE-0 to accomodate
        both (leader vs follower) roles it might end up playing. It can
        avoid this extra work if their_side is available early.

        If this protocol is selected, the `submit_outbound_pake0()`
        method must be called before any inputs are provided, so the
        transcript can be updated with the exact contents of the complete
        PAKE-0 message.
        """

    def submit_outbound_pake0(pake0: MessageTuple) -> str:
        """Submit the complete PAKE-0 message.

        Call this some time after `start_pake0()` if version negotiation
        selected this protocol. Provide the complete PAKE-0 message that
        was sent on the wire (including `my_key_setup_versions:` and any
        properties added by other (losing) versions. This enables the
        message transcript to include everything that influenced the
        process, to prevent downgrade attacks.

        This returns the next phase wanted by the IKeySetup, which in
        practice will always be `pake`.

        This must be invoked before any calls to `input()`.
        """

    def start_pake1(code: str, their_side: str, pake0: MessageTuple) -> NextKeySetupInput:
        """Set the wormhole code and generate PAKE-1 components

        Call start_pake1 when this version was *not* used for the initial
        PAKE-0. This happens when we misplaced our optimism in the wrong
        versions, and must now "catch up" with a different one.

        The `pake0` argument must contain the (side, phase, body) of the
        outbound PAKE-0 message (the same that would have been provided to
        `submit_outbound_pake0()`), to provide the contents of the outbound
        PAKE-0 message, for the transcript.

        This will return the next `wanted` phase. The caller should queue
        all inbound key-setup messages, and submit them one at a time to
        `input()`, providing only the phase requested with this `wanted`
        value. This will start with `pake` (aka the inbound PAKE-0), and
        increment through zero or more PAKE-N phases, before requesting
        `version`. Once `wanted` is None, the IKeySetup is done with inbound
        messages, and no more should be submitted to `input()`.

        It will also return a list of KeySetupActions, including a Send for
        PAKE-1. If `input()` was called before `start_pake1()`, it might
        have additional Sends, and/or a HaveAllegedKey. All outbound actions
        should be performed before looping around to the next `wanted` input.
        """

    def input(side: str, phase: str, body: bytes) -> NextKeySetupInput:
        """Accept/queue/process an inbound key-setup message.

        The 'phase' must match the current 'wanted' phase. This will be a
        PAKE-n phase ("pake", "pake-1", "pake-2", etc) or the VERSION phase.

        Throws CrowdedError if more than one "side" is observed by a single
        instance (the caller should really reject these before input() is
        called).

        Input messages will be processed immediately., or queued until the
        arrival of some future event. Message processing might produce output
        actions. After each call to input(), the caller should invoke
        output() in a loop until it runs dry.

        input() might throw CrowdedError or WrongPasswordError, which are
        terminal and sticky.
        """
