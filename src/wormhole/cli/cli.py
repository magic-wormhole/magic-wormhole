from __future__ import print_function

import os
import time
start = time.time()

from sys import stderr, stdout  # noqa: E402
from textwrap import dedent, fill  # noqa: E402

import click  # noqa: E402
import six  # noqa: E402
from twisted.internet.defer import inlineCallbacks, maybeDeferred  # noqa: E402
from twisted.internet.task import react  # noqa: E402
from twisted.python.failure import Failure  # noqa: E402

from . import public_relay  # noqa: E402
from .. import __version__  # noqa: E402
from ..errors import (KeyFormatError, NoTorError,  # noqa: E402
                      ServerConnectionError,
                      TransferError, UnsendableFileError, WelcomeError,
                      WrongPasswordError)
from ..timing import DebugTiming  # noqa: E402

top_import_finish = time.time()


class Config(object):
    """
    Union of config options that we pass down to (sub) commands.
    """

    def __init__(self):
        # This only holds attributes which are *not* set by CLI arguments.
        # Everything else comes from Click decorators, so we can be sure
        # we're exercising the defaults.
        self.timing = DebugTiming()
        self.cwd = os.getcwd()
        self.stdout = stdout
        self.stderr = stderr
        self.tor = False  # XXX?


def _compose(*decorators):
    def decorate(f):
        for d in reversed(decorators):
            f = d(f)
        return f

    return decorate


ALIASES = {
    "tx": "send",
    "rx": "receive",
    "recieve": "receive",
    "recv": "receive",
}


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        cmd_name = ALIASES.get(cmd_name, cmd_name)
        return click.Group.get_command(self, ctx, cmd_name)


# top-level command ("wormhole ...")
@click.group(cls=AliasedGroup)
@click.option("--appid", default=None, metavar="APPID", help="appid to use")
@click.option(
    "--relay-url",
    default=public_relay.RENDEZVOUS_RELAY,
    envvar='WORMHOLE_RELAY_URL',
    metavar="URL",
    help="rendezvous relay to use",
)
@click.option(
    "--transit-helper",
    default=public_relay.TRANSIT_RELAY,
    envvar='WORMHOLE_TRANSIT_HELPER',
    metavar="tcp:HOST:PORT",
    help="transit relay to use",
)
@click.option(
    "--dump-timing",
    type=type(u""),  # TODO: hide from --help output
    default=None,
    metavar="FILE.json",
    help="(debug) write timing data to file",
)
@click.version_option(
    message="magic-wormhole %(version)s",
    version=__version__,
)
@click.pass_context
def wormhole(context, dump_timing, transit_helper, relay_url, appid):
    """
    Create a Magic Wormhole and communicate through it.

    Wormholes are created by speaking the same magic CODE in two
    different places at the same time.  Wormholes are secure against
    anyone who doesn't use the same code.
    """
    context.obj = cfg = Config()
    cfg.appid = appid
    cfg.relay_url = relay_url
    cfg.transit_helper = transit_helper
    cfg.dump_timing = dump_timing


@inlineCallbacks
def _dispatch_command(reactor, cfg, command):
    """
    Internal helper. This calls the given command (a no-argument
    callable) with the Config instance in cfg and interprets any
    errors for the user.
    """
    cfg.timing.add("command dispatch")
    cfg.timing.add(
        "import", when=start, which="top").finish(when=top_import_finish)

    try:
        yield maybeDeferred(command)
    except (WrongPasswordError, NoTorError) as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=cfg.stderr)
        raise SystemExit(1)
    except (WelcomeError, UnsendableFileError, KeyFormatError) as e:
        msg = fill("ERROR: " + dedent(e.__doc__))
        print(msg, file=cfg.stderr)
        print(six.u(""), file=cfg.stderr)
        print(six.text_type(e), file=cfg.stderr)
        raise SystemExit(1)
    except TransferError as e:
        print(u"TransferError: %s" % six.text_type(e), file=cfg.stderr)
        raise SystemExit(1)
    except ServerConnectionError as e:
        msg = fill("ERROR: " + dedent(e.__doc__)) + "\n"
        msg += "(relay URL was %s)\n" % e.url
        msg += six.text_type(e)
        print(msg, file=cfg.stderr)
        raise SystemExit(1)
    except Exception as e:
        # this prints a proper traceback, whereas
        # traceback.print_exc() just prints a TB to the "yield"
        # line above ...
        Failure().printTraceback(file=cfg.stderr)
        print(u"ERROR:", six.text_type(e), file=cfg.stderr)
        raise SystemExit(1)

    cfg.timing.add("exit")
    if cfg.dump_timing:
        cfg.timing.write(cfg.dump_timing, cfg.stderr)


CommonArgs = _compose(
    click.option(
        "-0",
        "zeromode",
        default=False,
        is_flag=True,
        help="enable no-code anything-goes mode",
    ),
    click.option(
        "-c",
        "--code-length",
        default=2,
        metavar="NUMWORDS",
        help="length of code (in bytes/words)",
    ),
    click.option(
        "-v",
        "--verify",
        is_flag=True,
        default=False,
        help="display verification string (and wait for approval)",
    ),
    click.option(
        "--hide-progress",
        is_flag=True,
        default=False,
        help="supress progress-bar display",
    ),
    click.option(
        "--listen/--no-listen",
        default=True,
        help="(debug) don't open a listening socket for Transit",
    ),
)

TorArgs = _compose(
    click.option(
        "--tor",
        is_flag=True,
        default=False,
        help="use Tor when connecting",
    ),
    click.option(
        "--launch-tor",
        is_flag=True,
        default=False,
        help="launch Tor, rather than use existing control/socks port",
    ),
    click.option(
        "--tor-control-port",
        default=None,
        metavar="ENDPOINT",
        help="endpoint descriptor for Tor control port",
    ),
)


@wormhole.command()
@click.pass_context
def help(context, **kwargs):
    print(context.find_root().get_help())


# wormhole send (or "wormhole tx")
@wormhole.command()
@CommonArgs
@TorArgs
@click.option(
    "--code",
    metavar="CODE",
    help="human-generated code phrase",
)
@click.option(
    "--text",
    default=None,
    metavar="MESSAGE",
    help=("text message to send, instead of a file."
          " Use '-' to read from stdin."),
)
@click.option(
    "--ignore-unsendable-files",
    default=False,
    is_flag=True,
    help="Don't raise an error if a file can't be read.")
@click.argument("what", required=False, type=click.Path(path_type=type(u"")))
@click.pass_obj
def send(cfg, **kwargs):
    """Send a text message, file, or directory"""
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    with cfg.timing.add("import", which="cmd_send"):
        from . import cmd_send

    return go(cmd_send.send, cfg)


# this intermediate function can be mocked by tests that need to build a
# Config object
def go(f, cfg):
    # note: react() does not return
    return react(_dispatch_command, (cfg, lambda: f(cfg)))


# wormhole receive (or "wormhole rx")
@wormhole.command()
@CommonArgs
@TorArgs
@click.option(
    "--only-text",
    "-t",
    is_flag=True,
    help="refuse file transfers, only accept text transfers",
)
@click.option(
    "--accept-file",
    is_flag=True,
    help="accept file transfer without asking for confirmation",
)
@click.option(
    "--output-file",
    "-o",
    metavar="FILENAME|DIRNAME",
    help=("The file or directory to create, overriding the name suggested"
          " by the sender."),
)
@click.argument(
    "code",
    nargs=-1,
    default=None,
    #    help=("The magic-wormhole code, from the sender. If omitted, the"
    #          " program will ask for it, using tab-completion."),
)
@click.pass_obj
def receive(cfg, code, **kwargs):
    """
    Receive a text message, file, or directory (from 'wormhole send')
    """
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    with cfg.timing.add("import", which="cmd_receive"):
        from . import cmd_receive
    if len(code) == 1:
        cfg.code = code[0]
    elif len(code) > 1:
        print("Pass either no code or just one code; you passed"
              " {}: {}".format(len(code), ', '.join(code)))
        raise SystemExit(1)
    else:
        cfg.code = None

    return go(cmd_receive.receive, cfg)


@wormhole.group()
def ssh():
    """
    Facilitate sending/receiving SSH public keys
    """


@ssh.command(name="invite")
@click.option(
    "-c",
    "--code-length",
    default=2,
    metavar="NUMWORDS",
    help="length of code (in bytes/words)",
)
@click.option(
    "--user",
    "-u",
    default=None,
    metavar="USER",
    help="Add to USER's ~/.ssh/authorized_keys",
)
@TorArgs
@click.pass_context
def ssh_invite(ctx, code_length, user, **kwargs):
    """
    Add a public-key to a ~/.ssh/authorized_keys file
    """
    for name, value in kwargs.items():
        setattr(ctx.obj, name, value)
    from . import cmd_ssh
    ctx.obj.code_length = code_length
    ctx.obj.ssh_user = user
    return go(cmd_ssh.invite, ctx.obj)


@ssh.command(name="accept")
@click.argument(
    "code",
    nargs=1,
    required=True,
)
@click.option(
    "--key-file",
    "-F",
    default=None,
    type=click.Path(exists=True),
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt to send key",
)
@TorArgs
@click.pass_obj
def ssh_accept(cfg, code, key_file, yes, **kwargs):
    """
    Send your SSH public-key

    In response to a 'wormhole ssh invite' this will send public-key
    you specify (if there's only one in ~/.ssh/* that will be sent).
    """

    for name, value in kwargs.items():
        setattr(cfg, name, value)
    from . import cmd_ssh
    kind, keyid, pubkey = cmd_ssh.find_public_key(key_file)
    print("Sending public key type='{}' keyid='{}'".format(kind, keyid))
    if yes is not True:
        click.confirm(
            "Really send public key '{}' ?".format(keyid), abort=True)
    cfg.public_key = (kind, keyid, pubkey)
    cfg.code = code

    return go(cmd_ssh.accept, cfg)
