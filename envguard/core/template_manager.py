# envguard/core/template_manager.py
# Core logic for template checking and syncing.

from rich.console import Console
from rich.table import Table

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError
from envguard.parsers.factory import get_parser

console = Console()

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
        console.print(f"[red]Error:[/red] No suitable parser found for template file '{template_file}'.")
        return

    try:
        template_vars = template_parser.get_vars(template_file)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Template file '{template_file}' not found.")
        return

    # Aggregate all variables from the profile's source files
    source_vars = set()
    for link in links:
        source_file = link.get("source")
        if not source_file:
            continue

        source_parser = get_parser(source_file)
        if source_parser:
            try:
                source_vars.update(source_parser.get_vars(source_file))
            except FileNotFoundError:
                console.print(f"[yellow]Warning:[/yellow] Source file '{source_file}' not found. Skipping.")

    # --- Perform the comparison ---
    missing_in_source = template_vars - source_vars
    extra_in_source = source_vars - template_vars

    console.print(f"\n[bold]Template Check for Profile: [cyan]{profile_name}[/cyan][/bold]")
    console.print(f"  [bold]Template File:[/bold] [magenta]{template_file}[/magenta]")

    if not missing_in_source and not extra_in_source:
        console.print("[bold green]✓ Your environment files are perfectly in sync with the template![/bold green]")
        return

    # Display results in a table
    table = Table(title="Sync Status", border_style="blue")
    table.add_column("Status", style="cyan")
    table.add_column("Variable Name", style="white")
    table.add_column("Details", style="yellow")

    for var in sorted(list(missing_in_source)):
        table.add_row("[red]Missing[/red]", var, "Variable is in the template but not in your source files.")

    for var in sorted(list(extra_in_source)):
        table.add_row("[yellow]Extra[/yellow]", var, "Variable is in your source files but not in the template.")

    console.print(table)
    console.print("\n[bold]Suggestion:[/bold] Run `envguard template sync` to interactively update your template.")
