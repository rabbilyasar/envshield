# envguard/core/template_manager.py
# Core logic for template checking and syncing.

import os
import questionary
from rich.console import Console
from rich.table import Table
import datetime

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError
from envguard.parsers.factory import get_parser

console = Console()

def _get_sync_diff(profile_name: str):
    """Helper function to calculate differences for checking and syncing."""
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    template_file = profile_details.get("template")
    links = profile_details.get("links", [])

    if not template_file:
        return None, None, None, None

    template_parser = get_parser(template_file)
    if not template_parser:
        console.print(f"[red]Error:[/red] No suitable parser for template '{template_file}'.")
        return None, None, None, None

    try:
        template_vars = template_parser.get_vars(template_file)
    except FileNotFoundError:
        raise FileNotFoundError(f"Template file '{template_file}' not found.")

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

    missing_in_source = template_vars - source_vars
    extra_in_source = source_vars - template_vars

    return template_file, missing_in_source, extra_in_source, config

def check_template(profile_name: str):
    """Compares a profile's source file(s) against its template file."""
    try:
        template_file, missing, extra, _ = _get_sync_diff(profile_name)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if template_file is None:
        console.print(f"[yellow]Profile '{profile_name}' has no template file defined. Nothing to check.[/yellow]")
        return

    console.print(f"\n[bold]Template Check for Profile: [cyan]{profile_name}[/cyan][/bold]")
    console.print(f"  [bold]Template File:[/bold] [magenta]{template_file}[/magenta]")

    if not missing and not extra:
        console.print("[bold green]✓ Your environment files are perfectly in sync with the template![/bold green]")
        return

    table = Table(title="Sync Status", border_style="blue")
    table.add_column("Status", style="cyan")
    table.add_column("Variable Name", style="white")
    table.add_column("Details", style="yellow")

    for var in sorted(list(missing)):
        table.add_row("[red]Missing[/red]", var, "In template, but not in source files.")

    for var in sorted(list(extra)):
        table.add_row("[yellow]Extra[/yellow]", var, "In source files, but not in template.")

    console.print(table)
    console.print("\n[bold]Suggestion:[/bold] Run `envguard template sync` to interactively update your template.")


def _append_to_template(template_file: str, variables_to_add: list):
    """Appends new variables to the specified template file."""
    if not variables_to_add:
        return

    console.print(f"\nUpdating template file: [magenta]{template_file}[/magenta]")

    is_python_file = template_file.endswith('.py')

    try:
        with open(template_file, 'a') as f:
            f.write("\n\n# === Variables added by EnvGuard on {} ===\n".format(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            for var in sorted(variables_to_add):
                if is_python_file:
                    line = f'{var} = ""  # TODO: Add default value\n'
                else: # .env style
                    line = f'{var}=\n'
                f.write(line)
        console.print("[green]✓ Template file updated successfully.[/green]")
    except IOError as e:
        console.print(f"[red]Error:[/red] Could not write to template file: {e}")


def sync_template(profile_name: str):
    """Interactively syncs the template file with extra variables from source files."""
    try:
        template_file, _, extra_in_source, _ = _get_sync_diff(profile_name)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if template_file is None:
        console.print(f"[yellow]Profile '{profile_name}' has no template file defined. Nothing to sync.[/yellow]")
        return

    if not extra_in_source:
        console.print("[bold green]✓ Template is already up-to-date. No extra variables found.[/bold green]")
        return

    console.print("\n[bold]Interactive Template Sync[/bold]")
    console.print("The following variables exist in your source files but not in your template.")

    try:
        variables_to_add = questionary.checkbox(
            'Which variables would you like to add to the template?',
            choices=[questionary.Choice(title=var, checked=True) for var in sorted(list(extra_in_source))]
        ).ask()
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Sync cancelled by user.[/yellow]")
        return

    if not variables_to_add:
        console.print("No variables selected. Template remains unchanged.")
        return

    _append_to_template(template_file, variables_to_add)
