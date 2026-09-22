from attrs import frozen
from zope.interface import implementer

from ..util import (bytes_to_dict, bytes_to_hexstr, dict_to_bytes,
                    hexstr_to_bytes, derive_phase_key, HKDF,
                    encrypt_data, decrypt_data, CryptoError)
from ..errors import CrowdedError, WrongPasswordError
from . import ikeysetup
from .ikeysetup import MessageTuple, NextKeySetupInput
from .spake2_helper import SPAKE2_Helper
from cryptography.hazmat.primitives.asymmetric import mlkem
from .hash_transcript import hash_transcript
from .next_phase import next_phase

# "v2" is SPAKE-2 + ML-KEM hybrid, to protect against "slow" quantum
# computers and a Harvest Now, Decrypt Later attack.

# states
@frozen
class Init:
    pass
@frozen
class StartedEarly: # waiting for outbound PAKE-0
    pass
@frozen
class WantPAKE: # -> VerifyingOurVersion
    wanted: str
@frozen
class VerifyingOurVersion: # -> VerifyingKey
    kcm_key: bytes
    main_key: bytes
    wanted: str
@frozen
class VerifyingKey: # -> Done
    kcm_key: bytes
    main_key: bytes
@frozen
class Done:
    pass

@frozen
class Leader:
    pass
@frozen
class Follower:
    pass
@frozen
class Unknown:
    pass
Role = Leader | Follower | Unknown


@implementer(ikeysetup.IKeySetup)
class KeySetup_V2:
    VERSION = "v2"

    def __init__(self, side, appid, app_versions, timing, spake2_helper=None):
        self._side = side
        self._appid = appid
        self._app_versions = app_versions
        self._timing = timing
        if not spake2_helper:
            spake2_helper = SPAKE2_Helper(appid)
        assert isinstance(spake2_helper, SPAKE2_Helper)
        self._sph = spake2_helper

        self._error = None

        self._their_side = None
        self._transcript: list[MessageTuple] = []
        self._next_outbound_phase = "pake" # PAKE-0
        self._outputs: list[ikeysetup.KeySetupAction] = []

        self._role: Role = Unknown()
        self._need_to_send_spake2 = True
        self._need_to_send_mlkem_pubkey = True # speculative
        self._need_to_send_mlkem_ct = False # depends on role
        self._spake2_key = None
        self._mlkem_key = None
        self._state = Init()
        self._mlkem_privkey: mlkem.MLKEM768PrivateKey | None = None
        self._mlkem_pubkey: mlkem.MLKEM768PublicKey | None = None

    def _determine_leader(self, their_side):
        self._want_spake2 = True # for both
        if their_side is not None:
            if self._side > their_side:
                self._role = Leader() # KeyGen and Decapsulate
                self._need_to_send_mlkem_pubkey = False # sent
                self._want_mlkem_pubkey = False
                self._want_mlkem_ct = True
            elif their_side > self._side:
                self._role = Follower() # Encapsulate
                self._need_to_send_mlkem_pubkey = False # unneeded
                self._want_mlkem_pubkey = True
                self._want_mlkem_ct = False
            else:
                assert False, "side collision" # filtered elsewhere

    def start_pake0(self, code: str, their_side: str | None) -> dict:
        self._determine_leader(their_side) # maybe unknown
        assert self._state == Init()
        msg1 = self._sph.start(code)
        self._state = StartedEarly()
        msg = {"pake_v1": bytes_to_hexstr(msg1)}
        self._need_to_send_spake2 = False # sent
        print("-building pake0, I am", self._role)
        match self._role:
            case Leader() | Unknown():
                # leader does KeyGen and Decapsulate
                self._privkey = mlkem.MLKEM768PrivateKey.generate()
                pubkey = self._privkey.public_key()
                pubkey_b = pubkey.public_bytes_raw() # TODO PEM? DER?
                msg["v2_mlkem_pubkey"] = bytes_to_hexstr(pubkey_b)
            case Follower():
                pass # wait for leader's pubkey
            case _:
                raise ValueError("bad state")
        print(" outbound pake0 has", list(msg))
        return msg

    def submit_outbound_pake0(self, pake0mt: MessageTuple):
        assert self._state == StartedEarly()
        assert isinstance(pake0mt, tuple)
        self._transcript.append(pake0mt)
        self._next_outbound_phase = "pake-1"
        wanted = "pake"
        self._state = WantPAKE(wanted)
        return wanted

    def start_pake1(self, code: str, their_side: str, pake0mt: MessageTuple) -> NextKeySetupInput:
        assert self._state == Init()
        assert isinstance(pake0mt, tuple)
        self._transcript.append(pake0mt)
        self._determine_leader(their_side) # always known by now
        assert self._role in [Leader(), Follower()]
        print("-building pake1, I am", self._role)

        components = {}
        msg1 = self._sph.start(code)
        msg1_hex = bytes_to_hexstr(msg1)

        # the outbound PAKE-0 probably included a copy of
        # pake_v1. (reaching start_pake1 means we weren't
        # v2-optimistic, but we probably *were* v0- or v1- optimistic,
        # and those include the same data). Avoid sending a duplicate
        # by checking the outbound. But send one if necessary (which
        # happens if we're neither v0/v1/v2-optimistic *and* whatever
        # v3+ we *were* optimistic about doesn't use SPAKE2, and then
        # we negotiated down to v2).
        (p0_side, p0_phase, p0_body) = pake0mt
        p0_pieces = bytes_to_dict(p0_body)
        if "pake_v1" in p0_pieces:
            assert p0_pieces["pake_v1"] == msg1_hex
        else:
            components["pake_v1"] = msg1_hex
        self._need_to_send_spake2 = False

        # if we someday have a v3+ that uses ML-KEM too, do the same
        # check as above, to avoid duplicating the pubkey

        if self._role == Leader():
            # leader does KeyGen and Decapsulate
            self._privkey = mlkem.MLKEM768PrivateKey.generate()
            pubkey = self._privkey.public_key()
            pubkey_b = pubkey.public_bytes_raw() # TODO PEM? DER?
            components["v2_mlkem_pubkey"] = bytes_to_hexstr(pubkey_b)
        elif self._role == Follower():
            # wait for their pubkey, which will probably arrive in the
            # input(PAKE-0) we're about to receive. We'll send the CT
            # in our PAKE-2, which will be pipelined right behind the
            # PAKE-1 that this function is building, so it's just as
            # fast as if we'd bundled it in our PAKE-1
            pass

        print(" outbound pake1 has", list(components))
        body = dict_to_bytes(components)
        send = ikeysetup.Send(self._side, "pake-1", body)
        self._next_outbound_phase = "pake-2"
        wanted = "pake"
        self._state = WantPAKE(wanted)
        return (wanted, [send])

    def input(self, side: str, phase: str, body: bytes) -> NextKeySetupInput:
        print("INPUT(%s)" % phase)
        print(" to state", self._state)
        assert isinstance(side, str), type(phase)
        assert isinstance(phase, str), type(phase)
        assert isinstance(body, bytes), type(body)
        if self._their_side is None:
            self._their_side = side
            self._determine_leader(self._their_side) # always known
        if self._their_side != side:
            self._error = self._error or CrowdedError()
        if self._error:
            raise self._error
        assert self._role in [Leader(), Follower()]
        actions = False
        next_wanted = False
        match self._state:
            case Init():
                raise ValueError("input() before start")
            case StartedEarly():
                raise ValueError("input() before submit_outbound_pake0")
            case WantPAKE(wanted):
                print("WantPAKE (%s) start: wants: " % self._role,
                      "spake2 " if self._want_spake2 else "",
                      "mlkem_pubkey " if self._want_mlkem_pubkey else "",
                      "mlkem_ct " if self._want_mlkem_ct else "",
                      "have keys: ",
                      "spake2 " if self._spake2_key else "",
                      "mlkem " if self._mlkem_key else "")
                actions = []
                assert phase == wanted
                self._transcript.append((side, phase, body))
                assert self._want_spake2 or self._want_mlkem_pubkey or self._want_mlkem_ct
                payload = bytes_to_dict(body)
                print(" got", list(payload))
                if self._want_spake2 and "pake_v1" in payload:
                    # this lets us build the SPAKE2 portion of our key
                    msg2 = hexstr_to_bytes(payload["pake_v1"])
                    assert isinstance(msg2, bytes)
                    with self._timing.add("pake2", waiting="crypto"):
                        self._spake2_key = self._sph.finish(msg2)
                    self._want_spake2 = False
                if self._want_mlkem_pubkey and "v2_mlkem_pubkey" in payload:
                    assert self._role == Follower()
                    self._privkey = None # cancel speculation
                    # TODO: I don't see any PEM/DER deserialize
                    # methods, only raw
                    pk_bytes = hexstr_to_bytes(payload["v2_mlkem_pubkey"])
                    pubkey = mlkem.MLKEM768PublicKey.from_public_bytes(pk_bytes)
                    (self._mlkem_key, ct) = pubkey.encapsulate()
                    self._want_mlkem_pubkey = False
                    ct_msg = { "v2_mlkem_ciphertext": bytes_to_hexstr(ct) }
                    ct_body = dict_to_bytes(ct_msg)
                    outbound_phase = self._next_outbound_phase
                    self._next_outbound_phase = next_phase(outbound_phase)
                    actions.append(ikeysetup.Send(self._side, outbound_phase, ct_body))
                    self._transcript.append((self._side, outbound_phase, ct_body))
                if self._want_mlkem_ct and "v2_mlkem_ciphertext" in payload:
                    assert self._role == Leader()
                    assert self._privkey
                    ct = hexstr_to_bytes(payload["v2_mlkem_ciphertext"])
                    self._mlkem_key = self._privkey.decapsulate(ct)
                    self._want_mlkem_ct = False
                print("WantPAKE (%s) later: wants: " % self._role,
                      "spake2 " if self._want_spake2 else "",
                      "mlkem_pubkey " if self._want_mlkem_pubkey else "",
                      "mlkem_ct " if self._want_mlkem_ct else "",
                      "have keys: ",
                      "spake2 " if self._spake2_key else "",
                      "mlkem " if self._mlkem_key else "")
                
                assert self._spake2_key or self._want_spake2
                assert self._mlkem_key or (self._want_mlkem_pubkey or self._want_mlkem_ct)
                if self._spake2_key and self._mlkem_key:
                    kcm_key, main_key = self._compute_session_key()
                    actions.append(ikeysetup.HaveAllegedKey())
                    actions.append(self._send_pre_version())
                    actions.append(self._send_version(kcm_key))
                    next_wanted = next_phase(phase)
                    self._state = VerifyingOurVersion(kcm_key, main_key, next_wanted)
                    print(" waiting for pre-VERSION", next_wanted)
                else:
                    # still waiting for all the pieces
                    next_wanted = next_phase(phase)
                    self._state = WantPAKE(next_wanted)
                    print(" still waiting for key pieces, next phase", next_wanted)
            case VerifyingOurVersion(kcm_key, main_key, wanted):
                assert phase == wanted
                # *not* added to transcript
                payload = bytes_to_dict(body)
                print(" got", payload)
                if "our_key_setup_version" in payload:
                    their_version = payload["our_key_setup_version"]
                    if their_version != self.VERSION:
                        msg = ("version mismatch: me=%s, them=%s" %
                               (self.VERSION, their_version))
                        self._error = WrongPasswordError(msg)
                        raise self._error
                    actions = []
                    next_wanted = "version"
                    self._state = VerifyingKey(kcm_key, main_key)
                    print(" verified our_key_setup_version, next phase", next_wanted)
                else:
                    # keep waiting, WEIRD
                    actions = []
                    next_wanted = next_phase(phase)
                    self._state = VerifyingOurVersion(kcm_key, main_key, next_wanted)
                    print(" still hungry for pre-VERSION, next phase", next_wanted)
            case VerifyingKey(kcm_key, main_key):
                assert phase == "version"
                data_key = derive_phase_key(kcm_key, side, phase)
                try:
                    plaintext = decrypt_data(data_key, body)
                except CryptoError:
                    self._error = WrongPasswordError("invalid KCM")
                    raise self._error
                next_wanted = None
                actions = [ikeysetup.Done(main_key, plaintext)]
                self._state = Done()
                print(" verified VERSION")
            case _:
                raise ValueError("bad state")
        assert isinstance(actions, list)
        assert next_wanted != False
        return actions, next_wanted

    def _compute_session_key(self):
        t_hash = hash_transcript(self.VERSION, self._transcript)
        print("_COMPUTE_SESSION_KEY")
        print("transcript: %d items" % len(self._transcript))
        print(" spake2:", bytes_to_hexstr(self._spake2_key))
        print(" mlkem :", bytes_to_hexstr(self._mlkem_key))
        print(" t_hash:", bytes_to_hexstr(t_hash))
        ikm = self._spake2_key + self._mlkem_key
        kcm_tag = b"magic-wormhole key setup key-confirmation"
        kcm_key = HKDF(ikm, 32, salt=t_hash, CTXinfo=kcm_tag)
        main_tag = b"magic-wormhole key setup main key"
        main_key = HKDF(ikm, 32, salt=t_hash, CTXinfo=main_tag)
        print(" key:", bytes_to_hexstr(main_key))
        return kcm_key, main_key

    def _send_pre_version(self):
        preversion = { "our_key_setup_version": self.VERSION }
        preversion_body = dict_to_bytes(preversion)
        phase = self._next_outbound_phase
        self._next_outbound_phase = next_phase(phase)
        return ikeysetup.Send(self._side, phase, preversion_body)

    def _send_version(self, kcm_key):
        data_key = derive_phase_key(kcm_key, self._side, "version")
        plaintext = dict_to_bytes(self._app_versions)
        encrypted = encrypt_data(data_key, plaintext)
        return ikeysetup.Send(self._side, "version", encrypted)
