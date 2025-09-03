# import fnmatch
import os

# import stat
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
# from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
# from rich.table import Table

from .config import manager as config_manager
from .core import profile_manager, scanner, template_manager
from .core.exceptions import EnvShieldException
# from .utils import git_utils

# --- Main App Setup ---
app = typer.Typer(
    name="envshield",
    help="🛡️ EnvShield: Your Environment's First Line of Defense.",
    rich_markup_mode="markdown",
    add_completion=False,
)
console = Console()
template_app = typer.Typer(
    name="template", help="Check and sync environment templates.", no_args_is_help=True
)
app.add_typer(template_app, name="template")


# --- Helper Functions ---
def _get_profile_or_active(profile: Optional[str]) -> str:
    """Gets the specified profile or falls back to the active one."""
    from envshield import state

    if profile:
        return profile

    active_profile = state.get_active_profile()
    if not active_profile:
        console.print(
            "[red]Error:[/red] No profile specified and no profile is active."
        )
        raise typer.Exit(code=1)

    console.print(f"Operating on active profile: [cyan]{active_profile}[/cyan]")
    return active_profile


# --- Commands ---
@app.command()
def init():
    """Initializes EnvShield in the current project directory."""
    console.print(
        Panel(
            "[bold cyan]Welcome to EnvShield Initialization![/bold cyan]\n\nThis wizard will help you set up your project.",
            title="🛡️ EnvShield",
            border_style="green",
        )
    )
    if config_manager.config_file_exists():
        overwrite = questionary.confirm(
            "An `envshield.yml` file already exists. Do you want to overwrite it?",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()

    try:
        project_name = questionary.text(
            "What is the name of your project?", default=os.path.basename(os.getcwd())
        ).ask()
        env_file = questionary.text(
            "What is your primary environment file for local development?",
            default=".env.dev",
        ).ask()
        has_template = questionary.confirm(
            "Do you use a template file (e.g., .env.example)?", default=True
        ).ask()
        template_file = None
        if has_template:
            template_file = questionary.text(
                "What is the name of your template file?", default=".env.template"
            ).ask()
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold]Generating your `envshield.yml` file...[/bold]")
    config_content = config_manager.generate_default_config_content(
        project_name, env_file, template_file
    )
    config_manager.write_config_file(config_content)
    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print("Run `envshield onboard dev` to populate your new environment.")


@app.command()
def onboard(
    profile: str = typer.Argument(
        ..., help="The profile to set up, e.g., 'local-dev'."
    ),
):
    """A guided walkthrough to set up a new environment profile."""
    try:
        profile_manager.onboard_profile(profile)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception:
        raise typer.Exit(code=1)


@app.command(name="list")
def list_profiles_command():
    """Lists all available profiles from your envshield.yml file."""
    try:
        profile_manager.list_profiles()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def use(
    profile: str = typer.Argument(..., help="The name of the profile to activate."),
):
    """Switches the active environment to the specified profile."""
    try:
        profile_manager.use_profile(profile)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@template_app.command("check")
def template_check(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="The profile to check. Defaults to active."
    ),
):
    """Checks if your environment files are in sync with the template."""
    try:
        profile_to_check = _get_profile_or_active(profile)
        template_manager.check_template(profile_to_check)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@template_app.command("sync")
def template_sync(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="The profile to sync. Defaults to active."
    ),
):
    """Interactively add variables from your source files to your template."""
    try:
        profile_to_sync = _get_profile_or_active(profile)
        template_manager.sync_template(profile_to_sync)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def scan(
    paths: List[str] = typer.Argument(
        None,
        help="Paths to files or directories to scan. Defaults to current directory.",
    ),
    staged: bool = typer.Option(
        False, "--staged", help="Only scan files staged for the next Git commit."
    ),
    exclude: Optional[List[str]] = typer.Option(
        None,
        "--exclude",
        "-e",
        help="Glob patterns to exclude. Can be used multiple times.",
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a custom envshield.yml configuration file.",
    ),
):
    """Scans files for hardcoded secrets."""
    console.print("\n[bold cyan]🛡️  Running EnvShield Secret Scanner...[/bold cyan]")
    try:
        scanner.run_scan(
            paths=paths,
            staged_only=staged,
            exclude_patterns=exclude,
            config_path=config,
        )
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command("install-hook")
def install_hook():
    """Installs the Git pre-commit hook to scan for secrets automatically."""
    try:
        scanner.install_pre_commit_hook()
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
