# envguard/core/profile_manager.py
# Contains the core business logic for managing environment profiles.

import os
import shutil
import questionary
import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.panel import Panel

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError, SourceFileNotFoundError
from envguard import state
from envguard.core import file_updater
from envguard.parsers.factory import get_parser

console = Console()

def list_profiles():
    """
    Loads profiles from the config and displays them in a formatted table.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})
    active_profile = state.get_active_profile()

    if not profiles:
        console.print("[yellow]No profiles found in 'envguard.yml'.[/yellow]")
        return

    table = Table(title="Available Profiles", border_style="blue", show_header=True, header_style="bold blue")
    table.add_column("Active", justify="center", style="green")
    table.add_column("Profile Name", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")

    for name, details in profiles.items():
        is_active = "✓" if name == active_profile else ""
        table.add_row(
            is_active,
            name,
            details.get("description", "")
        )

    console.print(table)

def use_profile(profile_name: str):
    """
    Switches the active environment by processing the 'links' for the profile.
    For each link, it creates a symlink from the source to the target.

    Args:
        profile_name: The name of the profile to activate.
    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    links = profile_details.get("links", [])

    if not links:
        console.print(f"[yellow]Warning:[/yellow] Profile '{profile_name}' has no 'links' defined. No action taken.")
        return

    console.print(f"Switching to profile [bold cyan]{profile_name}[/bold cyan]...")

    # --- Symlink Logic ---
    for link in links:
        source_file = link.get("source")
        target_file = link.get("target")

        if not source_file or not target_file:
            console.print(f"[yellow]Warning:[/yellow] Skipping invalid link in profile '{profile_name}': {link}")
            continue

        if not os.path.exists(source_file):
            raise SourceFileNotFoundError(source_file)

        # If the target file (.env) already exists, we must remove it first.
        # It could be a real file or a symlink from a previous 'use' command.
        # os.lexists() is used to check for symlinks themselves without following them.
        if os.path.lexists(target_file):
            os.remove(target_file)

        # Create the symbolic link.
        try:
            os.symlink(source_file, target_file)
            console.print(
                f"  [green]✓[/green] Linked [magenta]{source_file}[/magenta] -> [bold magenta]{target_file}[/bold magenta]"
            )
        except OSError as e:
            console.print(f"[bold red]Error:[/bold red] Failed to create symbolic link for '{target_file}': {e}")
            console.print("On Windows, you may need to run this command as an administrator or enable Developer Mode.")
            # We stop on the first error to avoid a partially configured state.
            raise

    # Only set the profile as active if all links were successful.
    state.set_active_profile(profile_name)
    console.print("\n[bold green]Environment is now active![/bold green]")

def onboard_profile(profile_name: str):
    """
    Guides a new user through setting up a profile for the first time.
    1. Creates necessary source files from templates if they don't exist.
    2. Interactively prompts the user to fill in secret values.
    3. Activates the profile by running the 'use' logic.
    """
    console.print(Panel(
        f"[bold cyan]Welcome to EnvGuard Onboarding for the '[yellow]{profile_name}[/yellow]' profile![/bold cyan]\n\nThis wizard will get you set up and ready to code.",
        title="✨ Onboarding ✨",
        border_style="green"
    ))

    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    links = profile_details.get("links", [])
    template_file = profile_details.get("template")

    if not links:
        console.print(f"[yellow]Warning:[/yellow] Profile '{profile_name}' has no 'links' defined. Nothing to onboard.")
        return

    # --- Step 1: Create Missing Source Files from Template ---
    console.print("\n[bold]Step 1: Checking for necessary configuration files...[/bold]")
    created_files = False
    for link in links:
        source_file = link.get("source")
        if source_file and not os.path.exists(source_file):
            if not template_file:
                console.print(f"[red]Error:[/red] Source file '{source_file}' is missing, and no template is defined for this profile.")
                raise typer.Exit(code=1)

            # For simplicity, we assume all missing files can be created from the one master template.
            # A more advanced version could support per-link templates.
            console.print(f"  [yellow]![/yellow] Source file [magenta]{source_file}[/magenta] not found. Creating from template...")
            try:
                shutil.copy(template_file, source_file)
                created_files = True
            except IOError as e:
                console.print(f"[red]Error:[/red] Could not create '{source_file}' from template: {e}")
                raise typer.Exit(code=1)

    if created_files:
        console.print("[green]✓ All necessary source files have been created.[/green]")
    else:
        console.print("[green]✓ All source files already exist.[/green]")

   # --- Step 2: Interactively Populate Secrets ---
    console.print("\n[bold]Step 2: Let's configure your secrets.[/bold]")
    placeholders = ["changeme", "ask_your_team_lead", "get_this_from_1password_vault", ""]

    updates_by_file = {}

    for link in links:
        source_file = link.get("source")
        if not source_file:
            continue

        parser = get_parser(source_file)
        if not parser:
            continue

        try:
            # We need to read the values now, not just the keys
            # Let's add a temporary method to get key-value pairs
            # A better implementation would refactor parsers to return dicts
            with open(source_file, 'r') as f:
                lines = f.readlines()

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue

                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"\'') # Get raw value

                if value.lower() in [p.lower() for p in placeholders] or not value:
                    if value:
                        console.print(f"\nFound placeholder for [bold cyan]{key}[/bold cyan] in [magenta]{source_file}[/magenta].")
                    new_value = Prompt.ask(f"  Please enter the value for [bold cyan]{key}[/bold cyan]", password=True)

                    if source_file not in updates_by_file:
                        updates_by_file[source_file] = []
                    updates_by_file[source_file].append({'key': key, 'value': new_value})

        except (FileNotFoundError, IOError):
            continue # Skip files we can't read

    # --- Step 3: Save the Secrets ---
    if not updates_by_file:
        console.print("[green]✓ No placeholders found. Your secrets are already configured.[/green]")
    else:
        console.print("\n[bold]Step 3: Saving your secrets...[/bold]")
        for file_path, updates in updates_by_file.items():
            file_updater.update_variables_in_file(file_path, updates)
        console.print("[green]✓ All secrets have been securely saved to your local files.[/green]")

    # --- Step 4: Final Activation ---
    console.print("\n[bold]Step 4: Activating the environment...[/bold]")
    use_profile(profile_name)