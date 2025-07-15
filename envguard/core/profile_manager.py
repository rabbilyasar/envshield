# envguard/core/profile_manager.py
# Contains the core business logic for managing environment profiles.

import os
from rich.console import Console
from rich.table import Table

from envguard.config import manager as config_manager
from envguard.core.exceptions import ProfileNotFoundError, SourceFileNotFoundError
from envguard import state

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

