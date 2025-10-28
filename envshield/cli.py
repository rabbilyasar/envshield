# envshield/cli.py
import os
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel

from envshield.core import importer

from .config import manager as config_manager
from .core import schema_manager, scanner, doctor, inspector, setup_manager
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


# --- Commands ---
@app.command()
def init(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing EnvShield configuration files.",
    ),
):
    """Initializes EnvShield with intelligent, framework-aware defaults."""
    console.print(
        Panel(
            "[bold cyan]Welcome to EnvShield! Setting up your secure foundation...[/bold cyan]",
            title="🛡️ EnvShield",
            border_style="green",
        )
    )

    if os.path.exists(config_manager.CONFIG_FILE_NAME) and not force:
        console.print(
            "[yellow]An EnvShield setup already exists. Use '--force' to overwrite.[/yellow]"
        )
        raise typer.Exit()

    if force and os.path.exists(config_manager.CONFIG_FILE_NAME):
        import questionary

        overwrite = questionary.confirm(
            "Are you sure you want to overwrite your existing EnvShield configuration? This cannot be undone.",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()

    try:
        project_type = inspector.detect_project_type()
        if project_type:
            console.print(
                f"Detected a [bold yellow]{project_type}[/bold yellow] project."
            )
        else:
            console.print(
                "Could not detect a specific framework, using general defaults."
            )

        project_name = os.path.basename(os.getcwd())
        schema_content = config_manager.generate_default_schema_content(project_type)
        config_manager.write_file(
            config_manager.SCHEMA_FILE_NAME,
            schema_content,
            f"Created/updated schema: [bold cyan]{config_manager.SCHEMA_FILE_NAME}[/bold cyan]",
        )

        config_content = config_manager.generate_default_config_content(project_name)
        config_manager.write_file(
            config_manager.CONFIG_FILE_NAME,
            config_content,
            f"Created/updated config: [bold cyan]{config_manager.CONFIG_FILE_NAME}[/bold cyan]",
        )

        config_manager.update_gitignore()
        schema_manager.sync_schema()

        try:
            scanner.install_pre_commit_hook(non_interactive=True)
        except EnvShieldException as e:
            console.print(
                f"\n[bold yellow]⚠️  Warning:[/] Could not install Git hook: {e}"
            )
            console.print(
                "    You can install it later by running 'envshield install-hook' after initializing your Git repository."
            )

    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print(
        "Your project is now protected. Define your variables in 'env.schema.toml'."
    )


@app.command()
def check(
    file: str = typer.Argument(".env", help="The local environment file to validate."),
):
    """Validates a local environment file against the schema."""
    try:
        schema_manager.check_schema(file)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command(name="doctor")
def doctor_command(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Interactively attempt to fix any issues that are found.",
    ),
):
    """Runs a full health check on your project's EnvShield setup."""
    try:
        doctor.run_health_check(fix=fix)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@app.command()
def setup(
    output_file: str = typer.Argument(
        ".env", help="The name of the local environment file to create."
    ),
):
    """Interactively creates a local environment file from .env.example."""
    try:
        setup_manager.run_setup(output_file)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Setup cancelled by user.[/yellow]")
        raise typer.Exit()


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
    """Scans files for hardcoded secrets and undeclared variables."""
    try:
        scanner.run_scan(
            paths=paths,
            staged_only=staged,
            config_path=config,
            exclude_patterns=exclude,
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


@app.command(name="import")
def import_command(
    file: str = typer.Argument(
        ..., help="The .env file to import and convert to a schema."
    ),
    output: str = typer.Option(
        config_manager.SCHEMA_FILE_NAME,
        "--output",
        "-o",
        help="Path to write the new schema file to.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite the schema file if it already exists.",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Interactively guide you through classifying each variable.",
    ),
):
    """Generates an env.schema.toml from an existing .env file."""
    try:
        if os.path.exists(output) and not force and not interactive:
            console.print(
                f"[bold yellow]Warning:[/] Output file '{output}' already exists. Use --force to overwrite."
            )
            raise typer.Exit()

        if os.path.exists(output) and interactive:
            overwrite = questionary.confirm(
                f"Output file '{output}' already exists. Overwrite?"
            ).ask()
            if not overwrite:
                console.print("[yellow]Import cancelled.[/yellow]")
                raise typer.Exit()

        schema_content = importer.generate_schema_from_file(file, interactive)

        with open(output, "w") as f:
            f.write(schema_content)

        console.print(
            f"\nSuccessfully generated schema at [bold cyan]{output}[/bold cyan]"
        )

    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Import cancelled by user.[/yellow]")
        raise typer.Exit()
