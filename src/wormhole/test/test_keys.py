from __future__ import print_function, unicode_literals

import mock

from twisted.trial import unittest

from .._key import derive_key, derive_phase_key, encrypt_data, decrypt_data
from ..util import bytes_to_hexstr, hexstr_to_bytes


class Derive(unittest.TestCase):
    def test_derive_errors(self):
        self.assertRaises(TypeError, derive_key, 123, b"purpose")
        self.assertRaises(TypeError, derive_key, b"key", 123)
        self.assertRaises(TypeError, derive_key, b"key", b"purpose", "not len")

    def test_derive_key(self):
        m = "588ba9eef353778b074413a0140205d90d7479e36e0dd4ee35bb729d26131ef1"
        main = hexstr_to_bytes(m)

        dk1 = derive_key(main, b"purpose1")
        self.assertEqual(bytes_to_hexstr(dk1),
                         "835b5df80ce9ca46908e8524fb308649"
                         "122cfbcefbeaa7e65061c6ef08ee1b2a")

        dk2 = derive_key(main, b"purpose2", 10)
        self.assertEqual(bytes_to_hexstr(dk2), "f2238e84315b47eb6279")

    def test_derive_phase_key(self):
        m = "588ba9eef353778b074413a0140205d90d7479e36e0dd4ee35bb729d26131ef1"
        main = hexstr_to_bytes(m)

        dk11 = derive_phase_key(main, "side1", "phase1")
        self.assertEqual(bytes_to_hexstr(dk11),
                         "3af6a61d1a111225cc8968c6ca6265ef"
                         "e892065c3ab46de79dda21306b062990")

        dk12 = derive_phase_key(main, "side1", "phase2")
        self.assertEqual(bytes_to_hexstr(dk12),
                         "88a1dd12182d989ff498022a9656d1e2"
                         "806f17328d8bf5d8d0c9753e4381a752")

        dk21 = derive_phase_key(main, "side2", "phase1")
        self.assertEqual(bytes_to_hexstr(dk21),
                         "a306627b436ec23bdae3af8fa90c9ac9"
                         "27780d86be1831003e7f617c518ea689")

        dk22 = derive_phase_key(main, "side2", "phase2")
        self.assertEqual(bytes_to_hexstr(dk22),
                         "bf99e3e16420f2dad33f9b1ccb0be146"
                         "2b253d639dacdb50ed9496fa528d8758")


class Encrypt(unittest.TestCase):
    def test_encrypt(self):
        k = "ddc543ef8e4629a603d39dd0307a51bb1e7adb9cb259f6b085c91d0842a18679"
        key = hexstr_to_bytes(k)
        plaintext = hexstr_to_bytes("edc089a518219ec1cee184e89d2d37af")
        self.assertEqual(len(plaintext), 16)
        nonce = hexstr_to_bytes("2d5e43eb465aa42e750f991e425bee48"
                                "5f06abad7e04af80")
        self.assertEqual(len(nonce), 24)
        with mock.patch("nacl.utils.random", return_value=nonce):
            encrypted = encrypt_data(key, plaintext)
        self.assertEqual(len(encrypted), 24 + 16 + 16)
        self.assertEqual(bytes_to_hexstr(encrypted),
                         "2d5e43eb465aa42e750f991e425bee48"
                         "5f06abad7e04af80fe318e39d0e4ce93"
                         "2d2b54b300c56d2cda55ee5f0488d63e"
                         "b1d5f76f7919a49a")

    def test_decrypt(self):
        k = "ddc543ef8e4629a603d39dd0307a51bb1e7adb9cb259f6b085c91d0842a18679"
        key = hexstr_to_bytes(k)
        encrypted = hexstr_to_bytes("2d5e43eb465aa42e750f991e425bee48"
                                    "5f06abad7e04af80fe318e39d0e4ce93"
                                    "2d2b54b300c56d2cda55ee5f0488d63e"
                                    "b1d5f76f7919a49a")

        decrypted = decrypt_data(key, encrypted)
        self.assertEqual(len(decrypted), len(encrypted) - 24 - 16)
        self.assertEqual(bytes_to_hexstr(decrypted),
                         "edc089a518219ec1cee184e89d2d37af")
