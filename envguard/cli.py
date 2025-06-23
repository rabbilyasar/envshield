# envguard/cli.py
import click
from rich.console import Console
from envguard.utils.display import print_error # Import print_error
from envguard.utils.error_handling import EnvGuardError # Import base EnvGuardError

console = Console()

@click.group()
@click.version_option(package_name='envguard')
def cli():
    """
    EnvGuard CLI: Secure Environment Variable Management and Secret Prevention.
    """
    pass

# Custom error handler for EnvGuardError
@cli.result_callback()
def process_result(result, **kwargs):
    """
    This callback is executed after each command.
    It catches EnvGuardError exceptions and prints them gracefully.
    """
    if isinstance(result, Exception) and isinstance(result, EnvGuardError):
        print_error(str(result))
        click.get_current_context().exit(1) # Exit with a non-zero code for errors
    elif isinstance(result, Exception):
        # Catch any other unexpected exceptions
        print_error(f"An unexpected error occurred: {result}")
        click.get_current_context().exit(1)


if __name__ == '__main__':
    try:
        cli()
    except EnvGuardError as e:
        print_error(str(e))
        exit(1)
    except Exception as e:
        print_error(f"An unexpected error occurred: {e}")
        exit(1)