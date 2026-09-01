from attrs import frozen
from zope.interface import Interface

## actions, returned by IKeySetup.output()

# Send means a key-setup message needs to be sent to the
# peer. "inTranscript" means it should also be added to the
# negotiation transcript
@frozen
class Send:
    body: bytes
    inTranscript: bool

# Add an inbound key-setup message to the negotiation transcript
@frozen
class AddToTranscript:
    side: str
    phase: str
    body: bytes

# Request a call to transcriptIs() with the final negotiation
# transcript
@frozen
class GetTranscript:
    pass

# Indicate that key setup has achieved a potential key and is waiting
# for the VERSION/key-confirmation-message to arrive so it can be
# verified
#
@frozen
class HaveAllegedKey:
    key: bytes # TODO remove

# Send the "VERSION" (phase="version") key-confirmation message. This
# is encrypted+MAC-ed by the KeySetup, and is never included in the
# transcript (which was finalized already and served as input to the
# key used for VERSION).
@frozen
class SendVersion:
    body: bytes

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

KeySetupAction = Send | AddToTranscript | GetTranscript | HaveAllegedKey | SendVersion | Done


class IKeySetup(Interface):
    def start(code: str, their_side: str | None) -> dict:
        """Set the wormhole code and generate the PAKE0 components.

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
        """

    def input(side: str, phase: str, body: bytes):
        """Accept/queue/process an inbound key-setup message.

        This includes all PAKE-n messages ("pake", "pake-1", "pake-2", etc),
        as well as the VERSION message. Throws CrowdedError if more than
        one "side" is observed by a single instance (the caller should really
        reject these before input() is called). Unwanted messages may cause
        a log-and-ignore call to log.err().

        Input messages might be processed immediately, or queued until the
        arrival of some future event. Message processing might produce output
        actions. After each call to input(), the caller should invoke
        output() in a loop until it runs dry.

        input() might throw CrowdedError, WrongPasswordError, or
        CausalityError, all of which are terminal and sticky.
        """

    def transcriptIs(transcript: list[(str, str, bytes)]):
        """Deliver the negotiation transcript, for hashing.

        To prevent downgrade attacks, key-setup protocols should hash the
        entire negotiation transcript into the final session key. The
        EncryptionCore keeps track of the transcript, based on
        Send(addToTranscript=True) and AddInboundToTranscript() actions
        from the IKeySetup. The key-setup knows which messages are part of
        the transcript (it excludes the later pre-version and VERSION
        messages), but it doesn't know the outbound phases of the PAKE-n
        messages, so the EncryptionCore must assemble those.
        """

    def output() -> KeySetupAction | None:
        """Pull the next output event, if any.

        Returns an "action tuple", which describes something the KeySetup
        machine wants to do next. The caller should perform the action and
        then call output() again, in a loop, until output() returns None. The
        valid actions are:

        * Send(body, inTranscript): send outbound key-setup message to the
          mailbox. "body" is bytes. "inTranscript" means the outbound message
          should be added to the transcript.
        * AddToTranscript(side, phase, body): add an inbound message to the
          transcript.
        * GetTranscript(): request a transcriptIs() input with the transcript
        * HaveAllegedKey(key): we have an alleged key
          # TODO: stop providing the key, leave it for "done"
        * SendVersion(body): send the phase="version" key-confirmation message
        * Done(key, version_data): the key and application version
          bytes should be delivered to the Boss.
        """
