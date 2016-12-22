from __future__ import print_function

import os
from os.path import expanduser, exists, join
from twisted.internet.defer import inlineCallbacks
from twisted.internet import reactor
import click

from .. import xfer_util

class PubkeyError(Exception):
    pass

def find_public_key(hint=None):
    """
    This looks for an appropriate SSH key to send, possibly querying
    the user in the meantime. DO NOT CALL after reactor.run as this
    (possibly) does blocking stuff like asking the user questions (via
    click.prompt())

    Returns a 3-tuple: kind, keyid, pubkey_data
    """

    if hint is None:
        hint = expanduser('~/.ssh/')
    else:
        if not exists(hint):
            raise PubkeyError("Can't find '{}'".format(hint))

    pubkeys = [f for f in os.listdir(hint) if f.endswith('.pub')]
    if len(pubkeys) == 0:
        raise PubkeyError("No public keys in '{}'".format(hint))
    elif len(pubkeys) > 1:
        got_key = False
        while not got_key:
            ans = click.prompt(
                "Multiple public-keys found:\n" + \
                "\n".join(["  {}: {}".format(a, b) for a, b in enumerate(pubkeys)]) + \
                "\nSend which one?"
            )
            try:
                ans = int(ans)
                if ans < 0 or ans >= len(pubkeys):
                    ans = None
                else:
                    got_key = True
                    with open(join(hint, pubkeys[ans]), 'r') as f:
                        pubkey = f.read()

            except Exception:
                got_key = False
    else:
        with open(join(hint, pubkeys[0]), 'r') as f:
            pubkey = f.read()
    parts = pubkey.strip().split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]

    return kind, keyid, pubkey


@inlineCallbacks
def accept(cfg, reactor=reactor):
    yield xfer_util.send(
        reactor,
        cfg.appid or u"lothar.com/wormhole/ssh-add",
        cfg.relay_url,
        data=cfg.public_key[2],
        code=cfg.code,
        use_tor=cfg.tor,
    )
    print("Key sent.")


@inlineCallbacks
def invite(cfg, reactor=reactor):

    def on_code_created(code):
        print("Now tell the other user to run:")
        print()
        print("wormhole ssh accept {}".format(code))
        print()

    if cfg.ssh_user is None:
        ssh_path = expanduser('~/.ssh/'.format(cfg.ssh_user))
    else:
        ssh_path = expanduser('~{}/.ssh/'.format(cfg.ssh_user))
    auth_key_path = join(ssh_path, 'authorized_keys')
    if not exists(auth_key_path):
        print("Note: '{}' not found; will be created".format(auth_key_path))
        if not exists(ssh_path):
            print("      '{}' doesn't exist either".format(ssh_path))
    else:
        try:
            open(auth_key_path, 'a').close()
        except OSError:
            print("No write permission on '{}'".format(auth_key_path))
            return
    try:
        os.listdir(ssh_path)
    except OSError:
        print("Can't read '{}'".format(ssh_path))
        return

    pubkey = yield xfer_util.receive(
        reactor,
        cfg.appid or u"lothar.com/wormhole/ssh-add",
        cfg.relay_url,
        None,  # allocate a code for us
        use_tor=cfg.tor,
        on_code=on_code_created,
    )

    parts = pubkey.split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]

    if not exists(auth_key_path):
        if not exists(ssh_path):
            os.mkdir(ssh_path, mode=0o700)
    with open(auth_key_path, 'a', 0o600) as f:
        f.write('{}\n'.format(pubkey.strip()))
    print("Appended key type='{kind}' id='{key_id}' to '{auth_file}'".format(
        kind=kind, key_id=keyid, auth_file=auth_key_path))
