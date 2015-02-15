from hashlib import sha256, sha1
import hmac
import six

def HKDF(SKM, dkLen, XTS=None, CTXinfo=b"", digest=sha256,
         _test_expected_PRK=None):
    assert isinstance(SKM, six.binary_type)
    assert isinstance(XTS, (six.binary_type,type(None)))
    assert isinstance(CTXinfo, six.binary_type)
    hlen = len(digest(b"").digest())
    assert dkLen <= hlen*255
    if XTS is None:
        XTS = b"\x00"*hlen
    # extract
    PRK = hmac.new(XTS, SKM, digest).digest()
    if _test_expected_PRK and _test_expected_PRK != PRK:
        raise ValueError("test failed")
    # expand
    blocks = []
    counter = 1
    t = b""
    while hlen*len(blocks) < dkLen:
        t = hmac.new(PRK, t+CTXinfo+six.int2byte(counter), digest).digest()
        blocks.append(t)
        counter += 1
    return b"".join(blocks)[:dkLen]

def power_on_self_test():
    from binascii import hexlify, unhexlify

    def _test(IKM, salt, info, L, PRK, OKM, digest=sha256):
        def remove_prefix(prefix, s):
            assert s.startswith(prefix)
            return s[len(prefix):]
        ikm = unhexlify(remove_prefix("0x", IKM))
        salt = unhexlify(remove_prefix("0x", salt))
        info = unhexlify(remove_prefix("0x", info))
        prk = unhexlify(remove_prefix("0x", PRK))
        okm = unhexlify(remove_prefix("0x", OKM))
        if digest is None:
            out = HKDF(ikm, L, salt, info, _test_expected_PRK=prk)
        else:
            out = HKDF(ikm, L, salt, info, digest=digest,
                       _test_expected_PRK=prk)
        if okm != out:
            raise ValueError("got %s, expected %s" % (hexlify(out), hexlify(okm)))

    # test vectors from RFC5869
    _test(IKM="0x0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
          salt="0x000102030405060708090a0b0c",
          info="0xf0f1f2f3f4f5f6f7f8f9",
          L=42,
          PRK=("0x077709362c2e32df0ddc3f0dc47bba63"
               "90b6c73bb50f9c3122ec844ad7c2b3e5"),
          OKM=("0x3cb25f25faacd57a90434f64d0362f2a"
               "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
               "34007208d5b887185865"))

    _test(IKM=("0x000102030405060708090a0b0c0d0e0f"
               "101112131415161718191a1b1c1d1e1f"
               "202122232425262728292a2b2c2d2e2f"
               "303132333435363738393a3b3c3d3e3f"
               "404142434445464748494a4b4c4d4e4f"),
          salt=("0x606162636465666768696a6b6c6d6e6f"
                "707172737475767778797a7b7c7d7e7f"
                "808182838485868788898a8b8c8d8e8f"
                "909192939495969798999a9b9c9d9e9f"
                "a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"),
          info=("0xb0b1b2b3b4b5b6b7b8b9babbbcbdbebf"
                "c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
                "d0d1d2d3d4d5d6d7d8d9dadbdcdddedf"
                "e0e1e2e3e4e5e6e7e8e9eaebecedeeef"
                "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"),
          L=82,
          PRK=("0x06a6b88c5853361a06104c9ceb35b45c"
               "ef760014904671014a193f40c15fc244"),
          OKM=("0xb11e398dc80327a1c8e7f78c596a4934"
               "4f012eda2d4efad8a050cc4c19afa97c"
               "59045a99cac7827271cb41c65e590e09"
               "da3275600c2f09b8367793a9aca3db71"
               "cc30c58179ec3e87c14c01d5c1f3434f"
               "1d87"))

    _test(IKM="0x0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
          salt="0x",
          info="0x",
          L=42,
          PRK=("0x19ef24a32c717b167f33a91d6f648bdf"
               "96596776afdb6377ac434c1c293ccb04"),
          OKM=("0x8da4e775a563c18f715f802a063c5a31"
               "b8a11f5c5ee1879ec3454e5f3c738d2d"
               "9d201395faa4b61a96c8"))

    _test(digest=sha1,
          IKM="0x0b0b0b0b0b0b0b0b0b0b0b",
          salt="0x000102030405060708090a0b0c",
          info="0xf0f1f2f3f4f5f6f7f8f9",
          L=42,
          PRK="0x9b6c18c432a7bf8f0e71c8eb88f4b30baa2ba243",
          OKM=("0x085a01ea1b10f36933068b56efa5ad81"
               "a4f14b822f5b091568a9cdd4f155fda2"
               "c22e422478d305f3f896"))
    _test(digest=sha1,
       IKM=("0x000102030405060708090a0b0c0d0e0f"
            "101112131415161718191a1b1c1d1e1f"
            "202122232425262728292a2b2c2d2e2f"
            "303132333435363738393a3b3c3d3e3f"
            "404142434445464748494a4b4c4d4e4f"),
       salt=("0x606162636465666768696a6b6c6d6e6f"
             "707172737475767778797a7b7c7d7e7f"
             "808182838485868788898a8b8c8d8e8f"
             "909192939495969798999a9b9c9d9e9f"
             "a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"),
       info=("0xb0b1b2b3b4b5b6b7b8b9babbbcbdbebf"
             "c0c1c2c3c4c5c6c7c8c9cacbcccdcecf"
             "d0d1d2d3d4d5d6d7d8d9dadbdcdddedf"
             "e0e1e2e3e4e5e6e7e8e9eaebecedeeef"
             "f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff"),
       L=82,
       PRK="0x8adae09a2a307059478d309b26c4115a224cfaf6",
       OKM=("0x0bd770a74d1160f7c9f12cd5912a06eb"
            "ff6adcae899d92191fe4305673ba2ffe"
            "8fa3f1a4e5ad79f3f334b3b202b2173c"
            "486ea37ce3d397ed034c7f9dfeb15c5e"
            "927336d0441f4c4300e2cff0d0900b52"
            "d3b4"))
    _test(digest=sha1,
          IKM="0x0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
          salt="0x",
          info="0x",
          L=42,
          PRK="0xda8c8a73c7fa77288ec6f5e7c297786aa0d32d01",
          OKM=("0x0ac1af7002b3d761d1e55298da9d0506"
               "b9ae52057220a306e07b6b87e8df21d0"
               "ea00033de03984d34918"))

    _test(digest=sha1,
          IKM="0x0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c",
          salt="0x",
          info="0x",
          L=42,
          PRK="0x2adccada18779e7c2077ad2eb19d3f3e731385dd",
          OKM=("0x2c91117204d745f3500d636a62f64f0a"
               "b3bae548aa53d423b0d1f27ebba6f5e5"
               "673a081d70cce7acfc48"))

    # finally test that HKDF() without a digest= uses SHA256

    _test(digest=None,
          IKM="0x0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
          salt="0x",
          info="0x",
          L=42,
          PRK=("0x19ef24a32c717b167f33a91d6f648bdf"
               "96596776afdb6377ac434c1c293ccb04"),
          OKM=("0x8da4e775a563c18f715f802a063c5a31"
               "b8a11f5c5ee1879ec3454e5f3c738d2d"
               "9d201395faa4b61a96c8"))
    #print "all test passed"

power_on_self_test()
