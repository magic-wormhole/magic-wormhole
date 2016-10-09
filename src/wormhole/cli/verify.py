
def verify(verifier_bytes):
    verifier = bytes_to_hexstr(verifier_bytes)
    while True:
        ok = six.moves.input("Verifier %s. ok? (yes/no): " % verifier)
        if ok.lower() == "yes":
            return (True, None)
        if ok.lower() == "no":
            err = "sender rejected verification check, abandoned transfer"
            return (False, err)
