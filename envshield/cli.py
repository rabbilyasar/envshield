# envshield/cli.py
import os
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel

from .config import manager as config_manager
from .core import scanner, schema_manager
from .core.exceptions import EnvShieldException

# --- Main App Setup ---
app = typer.Typer(
    name="envshield",
    help="🛡️ EnvShield: Your Environment's First Line of Defense.",
    rich_markup_mode="markdown",
    add_completion=False,
)
console = Console()
schema_app = typer.Typer(
    name="schema", help="Check and sync your environment schema.", no_args_is_help=True
)
app.add_typer(schema_app, name="schema")


# # --- Helper Functions ---
# def _get_profile_or_active(profile: Optional[str]) -> str:
#     """Gets the specified profile or falls back to the active one."""
#     from envshield import state

#     if profile:
#         return profile

#     active_profile = state.get_active_profile()
#     if not active_profile:
#         console.print(
#             "[red]Error:[/red] No profile specified and no profile is active."
#         )
#         raise typer.Exit(code=1)

#     console.print(f"Operating on active profile: [cyan]{active_profile}[/cyan]")
#     return active_profile


# --- Commands ---
@app.command()
def init():
    """Initializes EnvShield in the current project for best practices."""
    console.print(
        Panel(
            "[bold cyan]Welcome to EnvShield Initialization![/bold cyan]\n\nThis wizard will set up a secure, documented foundation for your project's configuration.",
            title="🛡️ EnvShield",
            border_style="green",
        )
    )
    if os.path.exists(config_manager.CONFIG_FILE_NAME) or os.path.exists(
        config_manager.SCHEMA_FILE_NAME
    ):
        overwrite = questionary.confirm(
            "An EnvShield setup already exists. Do you want to overwrite it?",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()

    try:
        project_name = questionary.text(
            "What is the name of your project?", default=os.path.basename(os.getcwd())
        ).ask()

        # Step 1: Create Schema
        schema_content = config_manager.generate_default_schema_content()
        config_manager.write_file(
            config_manager.SCHEMA_FILE_NAME,
            schema_content,
            f"Created schema: [bold cyan]{config_manager.SCHEMA_FILE_NAME}[/bold cyan]",
        )

        # Step 2: Create Config
        config_content = config_manager.generate_default_config_content(project_name)
        config_manager.write_file(
            config_manager.CONFIG_FILE_NAME,
            config_content,
            f"Created config: [bold cyan]{config_manager.CONFIG_FILE_NAME}[/bold cyan]",
        )

        # Step 3: Run First Sync
        console.print("\n[bold]Running initial schema sync...[/bold]")
        schema_manager.sync_schema()

        # Step 4: Install Hook
        install = questionary.confirm(
            "Install Git pre-commit hook to automatically prevent secret leaks?",
            default=True,
        ).ask()
        if install:
            scanner.install_pre_commit_hook()

    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print(
        "Your configuration is now managed by the schema in 'env.schema.toml'."
    )


@app.command()
def check(
    file: str = typer.Argument(".env", help="The local environment file to validate.")
):
    """Validates a local environment file against the schema."""
    try:
        schema_manager.check_schema(file)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@schema_app.command("sync")
def schema_sync():
    """Generates a .env.example file from your schema."""
    try:
        schema_manager.sync_schema()
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
    config: Optional[str] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a custom envshield.yml configuration file.",
    ),
    exclude: Optional[List[str]] = typer.Option(
        None,
        "--exclude",
        "-e",
        help="Glob patterns to exclude. Can be used multiple times.",
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


# @app.command()
# def onboard(
#     profile: str = typer.Argument(
#         ..., help="The profile to set up, e.g., 'local-dev'."
#     ),
# ):
#     """A guided walkthrough to set up a new environment profile."""
#     try:
#         profile_manager.onboard_profile(profile)
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)
#     except Exception:
#         raise typer.Exit(code=1)


# @app.command(name="list")
# def list_profiles_command():
#     """Lists all available profiles from your envshield.yml file."""
#     try:
#         profile_manager.list_profiles()
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# @app.command()
# def use(
#     profile: str = typer.Argument(..., help="The name of the profile to activate."),
# ):
#     """Switches the active environment to the specified profile."""
#     try:
#         profile_manager.use_profile(profile)
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# @template_app.command("check")
# def template_check(
#     profile: Optional[str] = typer.Option(
#         None, "--profile", "-p", help="The profile to check. Defaults to active."
#     ),
# ):
#     """Checks if your environment files are in sync with the template."""
#     try:
#         profile_to_check = _get_profile_or_active(profile)
#         template_manager.check_template(profile_to_check)
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# @template_app.command("sync")
# def template_sync(
#     profile: Optional[str] = typer.Option(
#         None, "--profile", "-p", help="The profile to sync. Defaults to active."
#     ),
# ):
#     """Interactively add variables from your source files to your template."""
#     try:
#         profile_to_sync = _get_profile_or_active(profile)
#         template_manager.sync_template(profile_to_sync)
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# @app.command()
# def scan(
#     paths: List[str] = typer.Argument(
#         None,
#         help="Paths to files or directories to scan. Defaults to current directory.",
#     ),
#     staged: bool = typer.Option(
#         False, "--staged", help="Only scan files staged for the next Git commit."
#     ),
#     exclude: Optional[List[str]] = typer.Option(
#         None,
#         "--exclude",
#         "-e",
#         help="Glob patterns to exclude. Can be used multiple times.",
#     ),
#     config: Optional[str] = typer.Option(
#         None,
#         "--config",
#         "-c",
#         help="Path to a custom envshield.yml configuration file.",
#     ),
# ):
#     """Scans files for hardcoded secrets."""
#     console.print("\n[bold cyan]🛡️  Running EnvShield Secret Scanner...[/bold cyan]")
#     try:
#         scanner.run_scan(
#             paths=paths,
#             staged_only=staged,
#             exclude_patterns=exclude,
#             config_path=config,
#         )
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# @app.command("install-hook")
# def install_hook():
#     """Installs the Git pre-commit hook to scan for secrets automatically."""
#     try:
#         scanner.install_pre_commit_hook()
#     except EnvShieldException as e:
#         console.print(f"[bold red]Error:[/bold red] {e}")
#         raise typer.Exit(code=1)


# if __name__ == "__main__":
#     app()
