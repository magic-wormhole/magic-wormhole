from __future__ import print_function

import time
start = time.time()
import traceback
from os.path import expanduser, exists
from textwrap import fill, dedent
from sys import stdout, stderr
from . import public_relay
from .. import __version__
from ..timing import DebugTiming
from ..errors import WrongPasswordError, WelcomeError, KeyFormatError
from twisted.internet.defer import inlineCallbacks, maybeDeferred, returnValue
from twisted.internet.task import react

import click
top_import_finish = time.time()


class Config(object):
    """
    Union of config options that we pass down to (sub) commands.
    """
    def __init__(self):
        # common options
        self.timing = DebugTiming()
        self.tor = None
        self.listen = None
        self.relay_url = u""
        self.transit_helper = u""
        # send/receive commands
        self.code = None
        self.code_length = 2
        self.verify = False
        self.hide_progress = False
        self.dump_timing = False
        self.stdout = stdout
        self.stderr = stderr
        self.zeromode = False
        self.accept_file = None
        self.output_file = None
        # send only
        self.text = None
        self.what = None


ALIASES = {
    "tx": "send",
    "rx": "receive",
}
class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        cmd_name = ALIASES.get(cmd_name, cmd_name)
        return click.Group.get_command(self, ctx, cmd_name)


# top-level command ("wormhole ...")
@click.group(cls=AliasedGroup)
@click.option(
    "--relay-url", default=public_relay.RENDEZVOUS_RELAY,
    metavar="URL",
    help="rendezvous relay to use",
)
@click.option(
    "--transit-helper", default=public_relay.TRANSIT_RELAY,
    metavar="tcp:HOST:PORT",
    help="transit relay to use",
)
@click.option(
    "-c", "--code-length", default=2,
    metavar="NUMWORDS",
    help="length of code (in bytes/words)",
)
@click.option(
    "-v", "--verify", is_flag=True, default=False,
    help="display (and wait for acceptance of) verification string",
)
@click.option(
    "--hide-progress", is_flag=True, default=False,
    help="supress progress-bar display",
)
@click.option(
    "--dump-timing", type=type(u""), # TODO: hide from --help output
    default=None,
    metavar="FILE.json",
    help="(debug) write timing data to file",
)
@click.option(
    "--no-listen", is_flag=True, default=False,
    help="(debug) don't open a listening socket for Transit",
)
@click.option(
    "--tor", is_flag=True, default=True,
    help="use Tor when connecting",
)
@click.version_option(
    message="magic-wormhole %(version)s",
    version=__version__,
)
@click.pass_context
def wormhole(ctx, tor, no_listen, dump_timing, hide_progress,
             verify, code_length, transit_helper, relay_url):
    """
    Create a Magic Wormhole and communicate through it.

    Wormholes are created by speaking the same magic CODE in two
    different places at the same time.  Wormholes are secure against
    anyone who doesn't use the same code.
    """
    ctx.obj = cfg = Config()
    ctx.tor = tor
    if no_listen:
        cfg.listen = False
    cfg.relay_url = relay_url
    cfg.transit_helper = transit_helper
    cfg.code_length = code_length
    cfg.verify = verify
    cfg.hide_progress = hide_progress
    cfg.dump_timing = dump_timing


@inlineCallbacks
def _dispatch_command(reactor, cfg, command):
    """
    Internal helper. This calls the give command (a no-argument
    callable) with the Config instance in cfg and interprets any
    errors for the user.
    """
    cfg.timing.add("command dispatch")
    cfg.timing.add("import", when=start, which="top").finish(when=top_import_finish)

    try:
        rtn = yield maybeDeferred(command)
    except WrongPasswordError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
    except WelcomeError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
        print(file=stderr)
        print(str(e), file=stderr)
    except KeyFormatError as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=stderr)
    except Exception as e:
        traceback.print_exc()
        print("ERROR:", e, file=stderr)
        raise SystemExit(1)

    cfg.timing.add("exit")
    if cfg.dump_timing:
        cfg.timing.write(cfg.dump_timing, stderr)
    returnValue(rtn)
        

# wormhole send (or "wormhole tx")
@wormhole.command()
@click.option(
    "zeromode", "-0", default=False, is_flag=True,
)
@click.option(
    "--code", metavar="CODE",
    help="human-generated code phrase",
)
@click.option(
    "--text", default=u'', metavar="MESSAGE",
    help="text message to send, instead of a file. Use '-' to read from stdin.",
)
@click.argument("what", default=u'')
@click.pass_obj
def send(cfg, what, text, code, zeromode):
    """Send a text message, file, or directory"""
    with cfg.timing.add("import", which="cmd_send"):
        from . import cmd_send
    cfg.what = what
    cfg.text = text
    cfg.zeromode = zeromode
    cfg.code = code

    react(_dispatch_command, (cfg, lambda: cmd_send.send(cfg)))


# wormhole receive (or "wormhole rx")
@wormhole.command()
@click.option(
    "--only-text", "-t", is_flag=True,
    help="refuse file transfers, only accept text transfers",
)
@click.option(
    "--accept-file", is_flag=True,
    help="accept file transfer without asking for confirmation",
)
@click.option(
    "--output-file", "-o",
    metavar="FILENAME|DIRNAME",
    help=("The file or directory to create, overriding the name suggested"
          " by the sender."),
)
@click.option(
    "-0", "zeromode", is_flag=True,
    help="enable no-code anything-goes mode",
)
@click.argument(
    "code", nargs=-1, default=None,
#    help=("The magic-wormhole code, from the sender. If omitted, the"
#          " program will ask for it, using tab-completion."),
)
@click.pass_obj
def receive(cfg, code, zeromode, output_file, accept_file, only_text):
    """
    Receive a text message, file, or directory (from 'wormhole send')
    """
    with cfg.timing.add("import", which="cmd_receive"):
        from . import cmd_receive
    cfg.zeromode = zeromode
    cfg.output_file = output_file
    cfg.accept_file = accept_file
    cfg.only_text = only_text
    if len(code) == 1:
        cfg.code = code[0]
    elif len(code) > 1:
        print(
            "Pass either no code or just one code; you passed"
            " {}: {}".format(len(code), ', '.join(code))
        )
        raise SystemExit(1)
    else:
        cfg.code = None

    result = []
    try:
        def dispatcher(reactor):
            d = _dispatch_command(reactor, cfg, lambda: cmd_receive.receive(cfg))
            d.addCallback(lambda x: result.append(x))
            return d
        react(dispatcher)
    except SystemExit:
        return result[0]


@wormhole.command(name="ssh-add")
@click.option(
    "-c", "--code-length", default=2,
    metavar="NUMWORDS",
    help="length of code (in bytes/words)",
)
@click.pass_context
def ssh_add(ctx, code_length):
    from wormhole import codes
    from random import randint
    # how to properly allocate a channel?
    code = codes.make_code(unicode(randint(0, 2000)), code_length)
    print("Now tell the other user to run:")
    print()
    print("   wormhole ssh-send {}".format(code))
    print()

    pubkey = ctx.invoke(
        receive,
        output_file='-',
        accept_file=True,
        only_text=True,
        code=[code],
    )
    parts = pubkey.split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]
    print("Received pubkey type='{}' id='{}'".format(kind, keyid))

    path = expanduser('~/.ssh/authorized_keys')
    if not exists(path):
        print("Note: '{}' not found".format(path))
    with open(path, 'a') as f:
        f.write('{}\n'.format(pubkey.strip()))
    print("Appended to '{}'".format(path))


@wormhole.command(name="ssh-send")
@click.argument(
    "code", nargs=1, required=True,
)
@click.pass_context
def ssh_send(ctx, code):
    with open(expanduser('~/.ssh/id_rsa.pub'), 'r') as f:
        pubkey = f.read()
    parts = pubkey.strip().split()
    kind = parts[0]
    keyid = 'unknown' if len(parts) <= 2 else parts[2]
    print("Sending public key type='{}' keyid='{}'".format(kind, keyid))
    ctx.invoke(
        send,
        text=pubkey,
        code=code,
    )
