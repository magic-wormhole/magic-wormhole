from __future__ import print_function
import click
from ..cli.cli import Config

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
