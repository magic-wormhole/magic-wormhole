from __future__ import print_function
import json
import click
from ..cli.cli import Config, _compose

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
    ctx.obj = Config()

def _validate_websocket_protocol_options(ctx, param, value):
    return list(_validate_websocket_protocol_option(option) for option in value)

def _validate_websocket_protocol_option(option):
    try:
        key, value = option.split("=", 1)
    except ValueError:
        raise click.BadParameter("format options as OPTION=VALUE")

    try:
        value = json.loads(value)
    except:
        raise click.BadParameter("could not parse JSON value for {}".format(key))

    return (key, value)

LaunchArgs = _compose(
    click.option(
        "--rendezvous", default="tcp:4000", metavar="tcp:PORT",
        help="endpoint specification for the rendezvous port",
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
        "--allow-list/--disallow-list", default=True,
        help="always/never send list of allocated nameplates",
    ),
    click.option(
        "--relay-database-path", default="relay.sqlite", metavar="PATH",
        help="location for the relay server state database",
    ),
    click.option(
        "--stats-json-path", default="stats.json", metavar="PATH",
        help="location to write the relay stats file",
    ),
    click.option(
        "--websocket-protocol-option", multiple=True, metavar="OPTION=VALUE",
        callback=_validate_websocket_protocol_options,
        help="a websocket server protocol option to configure",
    ),
)


@server.command()
@LaunchArgs
@click.pass_obj
def start(cfg, **kwargs):
    """
    Start a relay server
    """
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    from wormhole.server.cmd_server import start_server
    start_server(cfg)


@server.command()
@LaunchArgs
@click.pass_obj
def restart(cfg, **kwargs):
    """
    Re-start a relay server
    """
    for name, value in kwargs.items():
        setattr(cfg, name, value)
    from wormhole.server.cmd_server import restart_server
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
