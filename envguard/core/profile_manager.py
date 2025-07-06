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
        console.print("[bold yellow]No profiles found in 'envguard.yml'.[/bold yellow]")
        return
    table = Table(title="Available Profiles", border_style="bright_blue")
    table.add_column("Active", justify="center", style="green")
    table.add_column("Profile Name", style="cyan", no_wrap=True)
    table.add_column("Description", style="magenta")
    table.add_column("Description", style="white")

    for name, details in profiles.items():
        is_active = "✓" if name == active_profile else ""
        table.add_row(
            is_active,
            name,
            details.get("source", "N/A"),
            details.get("description", "")
        )

    console.print(table)

def use_profile(profile_name: str, target_file: str = ".env"):
    """
    Switches the active environment by creating a symlink.

    Args:
        profile_name: The name of the profile to set as active.
        target_file: The name of the symlink to create (the file your app reads).

    """
    config = config_manager.load_config()
    profiles = config.get("profiles", {})

    if profile_name not in profiles:
        raise ProfileNotFoundError(profile_name)

    profile_details = profiles[profile_name]
    source_file = profile_details.get("source")

    if not source_file:
        console.print(f"[red]Error:[/red] Profile '{profile_name}' has no 'source' file defined.")
        return

    if not os.path.exists(source_file):
        raise SourceFileNotFoundError(source_file)

    # --- Symlink Logic ---
    console.print(f"Switching to profile [bold cyan]{profile_name}[/bold cyan]...")

    # If the target file (.env) already exists, we must remove it first.
    # It could be a real file or a symlink from a previous 'use' command.
    if os.path.lexists(target_file):
        os.remove(target_file)

    # Create a symlink to the source file
    try:
        os.symlink(source_file, target_file)
        state.set_active_profile(profile_name)
        console.print(
            f"[green]✓[/green] Successfully linked [magenta]{source_file}[/magenta] to [bold magenta]{target_file}[/bold magenta]."
        )
        console.print("Your environment is now active!")
    except OSError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to create symbolic link: {e}")
        console.print("On Windows, you may need to run this command as an administrator.")

