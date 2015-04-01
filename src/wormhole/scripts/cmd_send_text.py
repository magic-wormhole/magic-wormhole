from __future__ import print_function

APPID = "lothar.com/wormhole/text-xfer"

def send_text(args):
    # we're sending
    import sys, json, binascii
    from wormhole.blocking.transcribe import Initiator, WrongPasswordError

    i = Initiator(APPID, args.relay_url)
    code = i.get_code(args.code_length)
    print("On the other computer, please run: wormhole receive-text")
    print("Wormhole code is: %s" % code)
    print("")

    if args.verify:
        verifier = binascii.hexlify(i.get_verifier())
        while True:
            ok = raw_input("Verifier %s. ok? (yes/no): " % verifier)
            if ok.lower() == "yes":
                break
            if ok.lower() == "no":
                print("verification rejected, abandoning transfer",
                      file=sys.stderr)
                reject_data = json.dumps({"error": "verification rejected",
                                          }).encode("utf-8")
                i.get_data(reject_data)
                return 1

    message = args.text
    data = json.dumps({"message": message,
                       }).encode("utf-8")
    try:
        them_bytes = i.get_data(data)
    except WrongPasswordError as e:
        print("ERROR: " + e.explain(), file=sys.stderr)
        return 1
    them_d = json.loads(them_bytes.decode("utf-8"))
    if them_d["message"] == "ok":
        print("text sent")
    else:
        print("error sending text: %r" % (them_d,))

