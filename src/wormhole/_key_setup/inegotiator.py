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
    def got_code(self, code: str) -> list[NegotiatorAction]:
        """The wormhole code has been established"""
    def ready(self) -> list[NegotiatorAction]:
        """We should stop waiting for a peer's PAKE-0"""
    def got_versions(self, their_side: str, their_versions: list[str]) -> list[NegotiatorAction]:
        """We have received our peer's key-setup version offer"""
    def got_key_setup_message(self, side: str, phase: str, body: bytes) -> list[NegotiatorAction]:
        """We received a key-setup message: pake/pake-N/version"""
