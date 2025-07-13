# Core logic for template checking and syncing.

from rich.console import Console
from rich.table import Table

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError
from envguard.parsers.factory import get_parser

console = Console()

def _get_sync_diff(profile_name: str):
    """Helper function to calculate differences for checking and syncing."""
    pass

def check_template(profile_name: str):
    """
    Compares a profile's source file(s) against its template file.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    template_file = profile_details.get("template")
    links = profile_details.get("links", [])

    if not template_file:
        console.print(f"[yellow]Profile '{profile_name}' has no template file defined. Nothing to check.[/yellow]")
        return

    # Get the parser for the template file
    template_parser = get_parser(template_file)
    if not template_parser:
        console.print(f"[red]No suitable parser found for template file '{template_file}'.[/red]")
        return

    try:
        template_vars = template_parser.get_vars(template_file)
    except FileNotFoundError:
        console.print(f"[red]Template file '{template_file}' not found.[/red]")
        return

    # Aggregate all variable from the profile's source files
    source_vars = set()
    for link in links:
        source_file = link.get("source")
        if not source_file:
            console.print(f"[yellow]Link '{link}' has no source file defined. Skipping.[/yellow]")
            continue

        source_parser = get_parser(source_file)
        if not source_parser:
            console.print(f"[red]No suitable parser found for source file '{source_file}'.[/red]")
            continue

        try:
            vars_from_source = source_parser.get_vars(source_file)
            source_vars.update(vars_from_source)
        except FileNotFoundError:
            console.print(f"[yellow]Warning:[/yellow] Source file '{source_file}' not found. Skipping.")
            continue

