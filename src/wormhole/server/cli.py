from __future__ import print_function

import click


# can put this back in to get this command as "wormhole server"
# instead
#from ..cli.cli import wormhole
#@wormhole.group()
@click.group()
@click.pass_context
def server(ctx):
    """
    Control a relay server (most users shouldn't need to worry
    about this and can use the default server).
    """
    # just leaving this pointing to wormhole.cli.cli.Config for now,
    # but if we want to keep wormhole-server as a separate command
    # should probably have our own Config without all the options the
    # server commands don't use
    from ..cli.cli import Config
    ctx.obj = Config()


@server.command()
@click.option(
    "--rendezvous", default="tcp:4000", metavar="tcp:PORT",
    help="endpoint specification for the rendezvous port",
)
@click.option(
    "--transit", default="tcp:4001", metavar="tcp:PORT",
    help="endpoint specification for the transit-relay port",
)
@click.option(
    "--advertise-version", metavar="VERSION",
    help="version to recommend to clients",
)
@click.option(
    "--blur-usage", default=None, type=int,
    metavar="SECONDS",
    help="round logged access times to improve privacy",
)
@click.option(
    "--no-daemon", "-n", is_flag=True,
    help="Run in the foreground",
)
@click.option(
    "--signal-error", is_flag=True,
    help="force all clients to fail with a message",
)
@click.pass_obj
def start(cfg, signal_error, no_daemon, blur_usage, advertise_version,
          transit, rendezvous):
    """
    Start a relay server
    """
    from wormhole.server.cmd_server import start_server
    cfg.no_daemon = no_daemon
    cfg.blur_usage = blur_usage
    cfg.advertise_version = advertise_version
    cfg.transit = str(transit)
    cfg.rendezvous = str(rendezvous)
    cfg.signal_error = signal_error

    start_server(cfg)


# XXX it would be nice to reduce the duplication between 'restart' and
# 'start' options...
@server.command()
@click.option(
    "--rendezvous", default="tcp:4000", metavar="tcp:PORT",
    help="endpoint specification for the rendezvous port",
)
@click.option(
    "--transit", default="tcp:4001", metavar="tcp:PORT",
    help="endpoint specification for the transit-relay port",
)
@click.option(
    "--advertise-version", metavar="VERSION",
    help="version to recommend to clients",
)
@click.option(
    "--blur-usage", default=None, type=int,
    metavar="SECONDS",
    help="round logged access times to improve privacy",
)
@click.option(
    "--no-daemon", "-n", is_flag=True,
    help="Run in the foreground",
)
@click.option(
    "--signal-error", is_flag=True,
    help="force all clients to fail with a message",
)
@click.pass_obj
def restart(cfg, signal_error, no_daemon, blur_usage, advertise_version,
            transit, rendezvous):
    """
    Re-start a relay server
    """
    from wormhole.server.cmd_server import restart_server
    cfg.no_daemon = no_daemon
    cfg.blur_usage = blur_usage
    cfg.advertise_version = advertise_version
    cfg.transit = str(transit)
    cfg.rendezvous = str(rendezvous)
    cfg.signal_error = signal_error

    restart_server(cfg)


@server.command()
@click.pass_obj
def stop(cfg):
    """
    Stop a relay server
    """
    from wormhole.server.cmd_server import stop_server
    stop_server(cfg)


@server.command(name="tail-usage")
@click.pass_obj
def tail_usage(cfg):
    """
    Follow the latest usage
    """
    from wormhole.server.cmd_usage import tail_usage
    tail_usage(cfg)


@server.command(name='count-channels')
@click.option(
    "--json", is_flag=True,
)
@click.pass_obj
def count_channels(cfg, json):
    """
    Count active channels
    """
    from wormhole.server.cmd_usage import count_channels
    cfg.json = json
    count_channels(cfg)


@server.command(name='count-events')
@click.option(
    "--json", is_flag=True,
)
@click.pass_obj
def count_events(cfg, json):
    """
    Count events
    """
    from wormhole.server.cmd_usage import count_events
    cfg.json = json
    count_events(cfg)
