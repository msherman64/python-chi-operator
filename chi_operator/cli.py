import logging

import click

from .lease import lease
from .network import network
from .node import node
from .user import user


@click.group()
@click.option('-v', '--verbose', 'verbose', count=True)
@click.pass_context
def cli(ctx, verbose):
    ctx.ensure_object(dict)
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=log_level)


cli.add_command(lease)
cli.add_command(network)
cli.add_command(node)
cli.add_command(user)


if __name__ == '__main__':
    cli(obj={})
