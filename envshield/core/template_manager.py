# envshield/core/template_manager.py
# Core logic for template checking and syncing, with per-link support.

import os
import questionary
from rich.console import Console
from rich.table import Table
import datetime

from envshield.config import manager as config_manager
from envshield.core.exceptions import ProfileNotFoundError
from envshield.parsers.factory import get_parser

console = Console()


def _get_link_diff(link: dict):
    """Helper to get the variable difference for a single link."""
    source_file = link.get("source")
    template_file = link.get("template")

    if not source_file or not template_file:
        return None, None

    source_parser = get_parser(source_file)
    template_parser = get_parser(template_file)

    if not source_parser or not template_parser:
        return None, None

    try:
        source_vars = source_parser.get_vars(source_file)
        template_vars = template_parser.get_vars(template_file)
    except FileNotFoundError:
        return None, None  # Handled by the calling function

    extra_in_source = source_vars - template_vars
    return extra_in_source, template_file


def check_template(profile_name: str):
    """
    Compares each link in a profile with its own specific template.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    links = profile_details.get("links", [])

    console.print(
        f"\n[bold]Template Check for Profile: [cyan]{profile_name}[/cyan][/bold]"
    )

    found_issues = False
    for link in links:
        source_file = link.get("source")
        template_file = link.get("template")

        if not template_file:
            continue  # Skip links without a template

        console.print(
            f"\n[bold]Checking Link:[/bold] [magenta]{source_file}[/magenta] vs [magenta]{template_file}[/magenta]"
        )

        try:
            extra_vars, _ = _get_link_diff(link)
            # We also need to get missing vars for the report
            template_vars = get_parser(template_file).get_vars(template_file)
            source_vars = get_parser(source_file).get_vars(source_file)
            missing_vars = template_vars - source_vars

            if not extra_vars and not missing_vars:
                console.print("[green]  ✓ In sync.[/green]")
                continue

            found_issues = True
            table = Table(show_header=True, header_style="bold blue")
            table.add_column("Status", style="cyan")
            table.add_column("Variable Name", style="white")

            for var in sorted(list(missing_vars)):
                table.add_row("[red]Missing in Source[/red]", var)
            for var in sorted(list(extra_vars)):
                table.add_row("[yellow]Extra in Source[/yellow]", var)

            console.print(table)

        except FileNotFoundError:
            console.print(
                f"[red]  Error: One of the files was not found. Please run 'envshield onboard'.[/red]"
            )
            found_issues = True

    if found_issues:
        console.print(
            "\n[bold]Suggestion:[/bold] Run `envshield template sync` to interactively update your templates."
        )
    else:
        console.print(
            "\n[bold green]✓ All configured templates are perfectly in sync![/bold green]"
        )


def _append_to_template(template_file: str, variables_to_add: list):
    """Appends new variables to the specified template file."""
    if not variables_to_add:
        return

    console.print(f"\nUpdating template file: [magenta]{template_file}[/magenta]")

    is_python_file = template_file.endswith(".py")

    try:
        with open(template_file, "a") as f:
            f.write(
                "\n\n# === Variables added by EnvShield on {} ===\n".format(
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
            )
            for var in sorted(variables_to_add):
                if is_python_file:
                    line = f'{var} = ""  # TODO: Add default value\n'
                else:  # .env style
                    line = f"{var}=\n"
                f.write(line)
        console.print("[green]✓ Template file updated successfully.[/green]")
    except IOError as e:
        console.print(f"[red]Error:[/red] Could not write to template file: {e}")


def sync_template(profile_name: str):
    """
    Interactively syncs templates for each link in a profile.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})
    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    links = profiles[profile_name].get("links", [])

    console.print(
        f"\n[bold]Interactive Template Sync for Profile: [cyan]{profile_name}[/cyan][/bold]"
    )

    synced_something = False
    for link in links:
        try:
            extra_vars, template_file = _get_link_diff(link)
        except FileNotFoundError:
            continue  # Skip if files don't exist

        if not extra_vars or not template_file:
            continue

        synced_something = True
        console.print(
            f"\nFound extra variables for template [magenta]{template_file}[/magenta]:"
        )

        try:
            variables_to_add = questionary.checkbox(
                f"Which variables to add to {template_file}?",
                choices=[
                    questionary.Choice(title=var, checked=True)
                    for var in sorted(list(extra_vars))
                ],
            ).ask()
        except (KeyboardInterrupt, TypeError):
            console.print("\n[yellow]Sync cancelled by user.[/yellow]")
            return

        if variables_to_add:
            _append_to_template(template_file, variables_to_add)

    if not synced_something:
        console.print(
            "\n[bold green]✓ All templates are already up-to-date![/bold green]"
        )
