from __future__ import print_function, unicode_literals
import unittest
from binascii import unhexlify #, hexlify
from hkdf import Hkdf

#def generate_KAT():
#    print("KAT = [")
#    for salt in (b"", b"salt"):
#        for context in (b"", b"context"):
#            skm = b"secret"
#            out = HKDF(skm, 64, XTS=salt, CTXinfo=context)
#            hexout = "  '%s' +\n  '%s'" % (hexlify(out[:32]),
#                                           hexlify(out[32:]))
#            print(" (%r, %r, %r,\n%s)," % (salt, context, skm, hexout))
#    print("]")

KAT = [
 ('', '', 'secret',
  '2f34e5ff91ec85d53ca9b543683174d0cf550b60d5f52b24c97b386cfcf6cbbf' +
  '9cfd42fd37e1e5a214d15f03058d7fee63dc28f564b7b9fe3da514f80daad4bf'),
 ('', 'context', 'secret',
  'c24c303a1adfb4c3e2b092e6254ed481c41d8955ba8ec3f6a1473493a60c957b' +
  '31b723018ca75557214d3d5c61c0c7a5315b103b21ff00cb03ebe023dc347a47'),
 ('salt', '', 'secret',
  'f1156507c39b0e326159e778696253122de430899a8df2484040a85a5f95ceb1' +
  'dfca555d4cc603bdf7153ed1560de8cbc3234b27a6d2be8e8ca202d90649679a'),
 ('salt', 'context', 'secret',
  '61a4f201a867bcc12381ddb180d27074408d03ee9d5750855e5a12d967fa060f' +
  '10336ead9370927eaabb0d60b259346ee5f57eb7ceba8c72f1ed3f2932b1bf19'),
]

class TestKAT(unittest.TestCase):
    # note: this uses SHA256
    def test_kat(self):
        for (salt, context, skm, expected_hexout) in KAT:
            expected_out = unhexlify(expected_hexout)
            for outlen in range(0, len(expected_out)):
                out = Hkdf(salt.encode("ascii"),
                           skm.encode("ascii")).expand(context.encode("ascii"),
                                                       outlen)
                self.assertEqual(out, expected_out[:outlen])

#if __name__ == '__main__':
#    generate_KAT()
