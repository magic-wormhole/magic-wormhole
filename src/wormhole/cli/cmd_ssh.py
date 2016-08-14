from __future__ import print_function

from os.path import expanduser, exists
from twisted.internet.defer import inlineCallbacks
from twisted.internet import reactor

from .. import xfer_util


def find_public_key():
    """
    This looks for an appropriate SSH key to send, possibly querying
    the user in the meantime.

    Returns a 3-tuple: kind, keyid, pubkey_data
    """

    # XXX FIXME don't blindly just send this one...
    with open(expanduser('~/.ssh/id_rsa.pub'), 'r') as f:
        pubkey = f.read()
    parts = pubkey.strip().split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]

    return kind, keyid, pubkey


@inlineCallbacks
def send(cfg, reactor=reactor):
    yield xfer_util.send(
        reactor,
        u"lothar.com/wormhole/ssh-add",
        cfg.relay_url,
        data=cfg.public_key[2],
        code=cfg.code,
        use_tor=cfg.tor,
    )
    print("Key sent.")


@inlineCallbacks
def add(cfg, reactor=reactor):

    def on_code_created(code):
        print("Now tell the other user to run:")
        print()
        print("wormhole ssh-send {}".format(code))
        print()

    pubkey = yield xfer_util.receive(
        reactor,
        u"lothar.com/wormhole/ssh-add",
        cfg.relay_url,
        None,  # allocate a code for us
        use_tor=cfg.tor,
        on_code=on_code_created,
    )

    parts = pubkey.split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]

    path = cfg.auth_file
    if path == '-':
        print(pubkey.strip())
    else:
        if not exists(path):
            print("Note: '{}' not found; will be created".format(path))
        with open(path, 'a') as f:
            f.write('{}\n'.format(pubkey.strip()))
        print("Appended key type='{kind}' id='{key_id}' to '{auth_file}'".format(
            kind=kind, key_id=keyid, auth_file=path))
