from __future__ import print_function

import click


# can put this back in to get this command as "wormhole server"
# instead
#from ..cli.cli import wormhole
#@wormhole.group()
@click.group()
@click.pass_context
def server(ctx): # this is the setuptools entrypoint for bin/wormhole-server
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


_click_decorators = [
    server.command(),
    click.option(
        "--rendezvous", default="tcp:4000", metavar="tcp:PORT",
        help="endpoint specification for the rendezvous port",
    ),
    click.option(
        "--transit", default="tcp:4001", metavar="tcp:PORT",
        help="endpoint specification for the transit-relay port",
    ),
    click.option(
        "--advertise-version", metavar="VERSION",
        help="version to recommend to clients",
    ),
    click.option(
        "--blur-usage", default=None, type=int,
        metavar="SECONDS",
        help="round logged access times to improve privacy",
    ),
    click.option(
        "--no-daemon", "-n", is_flag=True,
        help="Run in the foreground",
    ),
    click.option(
        "--signal-error", is_flag=True,
        help="force all clients to fail with a message",
    ),
    click.option(
        "--disallow-list", is_flag=True,
        help="never send list of allocated nameplates",
    ),
    click.option(
        "--relay-database-path", default="relay.sqlite", metavar="PATH",
        help="location for the relay server state database",
    ),
    click.option(
        "--stats-json-path", default="stats.json", metavar="PATH",
        help="location to write the relay stats file",
    ),
    click.pass_obj,
]


def _start_command(f):
    for dec in _click_decorators[::-1]:
        f = dec(f)
    return f


def arguments_to_config(
        cfg, signal_error, no_daemon, blur_usage, advertise_version,
        transit, rendezvous, disallow_list, relay_database_path,
        stats_json_path,
):
    cfg.no_daemon = no_daemon
    cfg.blur_usage = blur_usage
    cfg.advertise_version = advertise_version
    cfg.transit = str(transit)
    cfg.rendezvous = str(rendezvous)
    cfg.signal_error = signal_error
    cfg.allow_list = not disallow_list
    cfg.relay_database_path = relay_database_path
    cfg.stats_json_path = stats_json_path


@_start_command
def start(cfg, **arguments):
    """
    Start a relay server
    """
    from wormhole.server.cmd_server import start_server
    arguments_to_config(cfg, **arguments)
    start_server(cfg)


@_start_command
def restart(cfg, **arguments):
    """
    Re-start a relay server
    """
    from wormhole.server.cmd_server import restart_server
    arguments_to_config(cfg, **arguments)
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
