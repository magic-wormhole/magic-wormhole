from zope.interface import Interface
from attrs import frozen

# actions
@frozen
class Send:
    phase: str
    body: bytes
@frozen
class HaveAllegedKey:
    pass
@frozen
class Done:
    key: bytes
    version_data: bytes
NegotiatorAction = Send | HaveAllegedKey | Done


# inputs:
class INegotiator(Interface):
    def got_code(self, code: str) -> None:
        """The wormhole code has been established"""
    def ready(self) -> None:
        """We should stop waiting for a peer's PAKE-0"""
    def got_versions(self, their_side: str, their_versions: list[str]) -> None:
        """We have received our peer's key-setup version offer"""
    def got_key_setup_message(self, side: str, phase: str, body: bytes) -> None:
        """We received a key-setup message: pake/pake-N/version"""
    #
    def output(self) -> NegotiatorAction | None:
        """Pull the next output action, if any"""
