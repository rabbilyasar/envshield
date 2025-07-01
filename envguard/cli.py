# envguard/cli.py
import click
# Import the custom display and error handling utilities
from envguard.utils.display import print_error, console, print_warning # Import console for raw printing if needed
from envguard.utils.error_handling import EnvGuardError
import sys # For sys.exit


@click.group()
@click.version_option(package_name='envguard') # Gets version from setup.py
def cli():
    """
    EnvGuard CLI: Secure Environment Variable Management and Secret Prevention.

    EnvGuard streamlines local .env file management, enforces security best
    practices through pre-commit scanning, simplifies template synchronization,
    and facilitates secure sharing of non-sensitive environment snippets.
    """
    pass # No setup needed at the group level yet

# --- Centralized Error Handling ---
# This decorator registers a callback that runs after any command completes.
# It allows us to catch custom exceptions and print formatted error messages.
@cli.result_callback()
def process_result(result, **kwargs):
    """
    Callback function executed after each command.
    It catches custom EnvGuardError exceptions and prints them gracefully,
    ensuring a consistent error reporting experience.
    """
    if isinstance(result, Exception):
        if isinstance(result, EnvGuardError):
            print_error(str(result)) # Print custom EnvGuardError messages
            sys.exit(1) # Exit with a non-zero code to indicate failure
        elif isinstance(result, click.exceptions.Exit) and result.exit_code == 0:
            # Allow Click's clean exit (e.g., from --help or successful command)
            pass
        elif isinstance(result, click.exceptions.Abort):
            # User aborted an interactive prompt (e.g., Ctrl+C)
            print_warning("Operation aborted by user.")
            sys.exit(1)
        else:
            # Catch any other unexpected exceptions and report them generically.
            print_error(f"An unexpected internal error occurred: {result}")
            # For debugging, you might want to print the full traceback here:
            # console.print_exception(show_locals=True)
            sys.exit(1)
    # If result is not an exception, command completed successfully (or handled its own exit)
    sys.exit(0) # Explicitly exit 0 for success if no exception was raised

if __name__ == '__main__':
    # This block allows running the CLI directly for development/testing,
    # though the primary way to run is via the `envguard` console script.
    cli()