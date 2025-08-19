# envguard/core/profile_manager.py
# Contains the core business logic for managing environment profiles.

import os
import shutil
import subprocess
import questionary
import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.panel import Panel

from envguard.config import manager as config_manager
from envguard.core import file_updater
from envguard.core.exceptions import ProfileNotFoundError, SourceFileNotFoundError
from envguard import state
from envguard.parsers.factory import get_parser
from typing import Union, List


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


def _run_script(script_command: Union[str, List[str]]):
    """
    Runs a single shell command or a list of shell commands.
    Raises an exception if any script fails.
    """
    if not script_command:
        return

    commands = script_command if isinstance(script_command, list) else [script_command]

    for command in commands:
        console.print(f"\n[bold]Executing script:[/bold] [bright_black]{command}[/bright_black]")
        try:
            process = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )

            for line in process.stdout:
                console.print(f"[dim]  {line.strip()}[/dim]")

            process.wait()

            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, command)

            console.print(f"[green]✓ Script finished successfully.[/green]")

        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Command not found: '{command.split()[0]}'. Please ensure it's in your PATH.")
            raise
        except subprocess.CalledProcessError:
            console.print(f"[bold red]Error:[/bold red] Script failed with a non-zero exit code. Halting onboarding.")
            raise
        except Exception as e:
            console.print(f"[bold red]An unexpected error occurred while running the script: {e}[/bold red]")
            raise


def onboard_profile(profile_name: str):
    """
    Guides a new user through setting up a profile for the first time with
    smarter, more versatile logic and pre/post scripts.
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
    onboarding_prompts = profile_details.get("onboarding_prompts", [])
    pre_script = profile_details.get("pre_onboard_script")
    post_script = profile_details.get("post_onboard_script")

    if not links:
        console.print(f"[yellow]Warning:[/yellow] Profile '{profile_name}' has no 'links' defined. Nothing to onboard.")
        return

    # --- Step 0: Run Pre-Onboarding Script(s) ---
    if pre_script:
        console.print("\n[bold]Step 0: Running pre-onboarding validation script(s)...[/bold]")
        _run_script(pre_script)

    # --- Step 1: Create Missing Source Files ---
    console.print("\n[bold]Step 1: Checking for necessary configuration files...[/bold]")
    created_files = False
    for link in links:
        source_file = link.get("source")
        template_file = link.get("template")
        if source_file and not os.path.exists(source_file):
            console.print(f"  [yellow]![/yellow] Source file [magenta]{source_file}[/magenta] not found. Attempting to create it...")

            if template_file and os.path.exists(template_file):
                try:
                    shutil.copy(template_file, source_file)
                    console.print(f"    [green]✓[/green] Created from template '{template_file}'.")
                    created_files = True
                except IOError as e:
                    console.print(f"    [red]Error:[/red] Could not create '{source_file}': {e}")
                    raise typer.Exit(code=1)
            else:
                open(source_file, 'a').close()
                console.print(f"    [green]✓[/green] Created a blank file (no template specified for this link).")
                created_files = True

    if created_files:
        console.print("[green]✓ All necessary source files have been created.[/green]")
    else:
        console.print("[green]✓ All source files already exist.[/green]")

    # --- Step 2 & 3: Populate and Save Secrets ---
    console.print("\n[bold]Step 2: Let's configure your secrets.[/bold]")
    placeholder_keywords = ["changeme", "ask", "todo", "example", "dummy", "placeholder", "get from", "your-"]
    updates_by_file = {}
    for link in links:
        source_file = link.get("source")
        if not source_file or not os.path.exists(source_file):
            continue
        try:
            with open(source_file, 'r') as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                raw_value = value.strip().strip('"\'')
                is_explicit_prompt = key in onboarding_prompts
                is_placeholder = (
                    not raw_value or
                    raw_value.lower() == 'x' or
                    any(keyword in raw_value.lower() for keyword in placeholder_keywords)
                )
                if is_explicit_prompt or is_placeholder:
                    if any(update['key'] == key for updates in updates_by_file.values() for update in updates):
                        continue
                    console.print(f"\nFound variable to configure: [bold cyan]{key}[/bold cyan] in [magenta]{source_file}[/magenta].")
                    if raw_value:
                        console.print(f"  [dim]Current value/instruction: {raw_value}[/dim]")
                    new_value = Prompt.ask(f"  Please enter the value for [bold cyan]{key}[/bold cyan]", password=True)
                    if source_file not in updates_by_file:
                        updates_by_file[source_file] = []
                    updates_by_file[source_file].append({'key': key, 'value': new_value})
        except IOError:
            continue
    if not updates_by_file:
        console.print("[green]✓ No placeholders found to update. Your secrets appear to be configured.[/green]")
    else:
        for file_path, updates in updates_by_file.items():
            file_updater.update_variables_in_file(file_path, updates)
        console.print("[green]✓ All secrets have been securely saved to your local files.[/green]")

    # --- Step 3: Final Activation ---
    console.print("\n[bold]Step 3: Activating the environment...[/bold]")
    use_profile(profile_name)

    # --- Step 4: Run Post-Onboarding Script(s) ---
    if post_script:
        console.print("\n[bold]Step 4: Running post-onboarding setup script(s)...[/bold]")
        _run_script(post_script)

    console.print("\n[bold green]🎉 Onboarding Complete! Your environment is ready. 🎉[/bold green]")

