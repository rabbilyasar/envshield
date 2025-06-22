# envguard/cli.py
import click
from rich.console import Console

console = Console()

@click.group()
@click.version_option(package_name='envguard')
def cli():
    """
    EnvGuard CLI: Secure Environment Variable Management and Secret Prevention.
    """
    pass

if __name__ == '__main__':
    cli()