from attrs import frozen
from zope.interface import Interface

# Send means a key-setup message needs to be sent to the peer
@frozen
class Send:
    phase: str
    body: bytes

# HaveAllegedKey indicates that key setup has achieved a potential key
# and is waiting for the VERSION/key-confirmation-message to arrive so
# it can be verified
#
@frozen
class HaveAllegedKey:
    key: bytes

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

NegotiationOutput = Send | HaveAllegedKey | Done

class INegotiation(Interface):
    def start(code: str) -> dict:
        """Set the wormhole code and generate the PAKE0 components.

        Call this when the complete wormhole code is available and
        we've either received the peer's PAKE-0 (phase="pake") message
        or we know we shouldn't wait for it. It will be used by any
        PAKE algorithms involved in this particular version of the key
        setup protocol. The return value contains components to go into
        our outbound PAKE-0 message.
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

    def output() -> NegotiationOutput | None:
        """Pull the next output event, if any.

        Returns an "action tuple", which describes something the Negotiation
        machine wants to do next. The caller should perform the action and
        then call output() again, in a loop, until output() returns None. The
        valid actions are:

        * Send(phase, body): send outbound key-setup message to the mailbox.
          "phase" will specify a PAKE-n or VERSION phase. "body" is bytes.
        * HaveAllegedKey(key): we have an alleged key
          # TODO: stop providing the key, leave it for "done"
        * Done(key, version_data): the key and application version
          bytes should be delivered to the Boss.
        """
