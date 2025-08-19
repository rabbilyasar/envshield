# envguard/cli.py
from typing import List
import typer
import os
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from envguard.core import scanner
from envguard.utils import git_utils

from .config import manager as config_manager
from .core import profile_manager
from .core import template_manager
from .core.exceptions import EnvGuardException

app = typer.Typer(
    name="envguard",
    help="🛡️  A CLI tool to manage, secure, and collaborate on environment variables.",
    rich_markup_mode="markdown",
    add_completion=False
)

console = Console()

# --- Create a subcommand for 'template' ---
template_app = typer.Typer(
    name="template",
    help="Manage environment templates.",
    no_args_is_help=True
)
app.add_typer(template_app, name="template")

@template_app.command("check")
def template_check(
    profile: str = typer.Option(None, "--profile", "-p", help="The profile to check. Defaults to the active profile.")
):
    """Checks if your environment files are in sync with the template."""
    from envguard import state # Local import to avoid circular dependency

    try:
        if not profile:
            profile = state.get_active_profile()
            if not profile:
                console.print("[red]Error:[/red] No profile specified and no profile is active.")
                console.print("Please specify a profile with `--profile <name>` or run `envguard use <name>` first.")
                raise typer.Exit(code=1)
            console.print(f"Checking active profile: [cyan]{profile}[/cyan]")

        template_manager.check_template(profile)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

@template_app.command("sync")
def template_sync(
    profile: str = typer.Option(None, "--profile", "-p", help="The profile to sync. Defaults to the active profile.")
):
    """Interactively add variables from your source files to your template."""
    from envguard import state

    try:
        if not profile:
            profile = state.get_active_profile()
            if not profile:
                console.print("[red]Error:[/red] No profile specified and no profile is active.")
                console.print("Please specify a profile with `--profile <name>` or run `envguard use <name>` first.")
                raise typer.Exit(code=1)
            console.print(f"Syncing template for active profile: [cyan]{profile}[/cyan]")

        template_manager.sync_template(profile)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

# --- Main Commands ---

@app.command()
def init():
    """
    Initializes EnvGuard in the current project directory.
    """
    console.print(Panel(
        "[bold cyan]Welcome to EnvGuard Initialization![/bold cyan]\n\nThis wizard will help you set up your project in a few simple steps.",
        title="🛡️ EnvGuard",
        border_style="green"
    ))

    if config_manager.config_file_exists():
        overwrite = questionary.confirm(
            f"An `envguard.yml` file already exists. Do you want to overwrite it?",
            default=False
        ).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled. Your existing config file is safe.[/yellow]")
            raise typer.Exit()

    try:
        project_name = questionary.text(
            "What is the name of your project?",
            default=os.path.basename(os.getcwd())
        ).ask()
        # This part of init is less relevant for the multi-link setup, but we keep it for simple projects.
        # For complex projects like Issuebear, users will edit the yml manually.
        env_file = questionary.text(
            "What is your primary environment file for local development? (e.g., .env)",
            default=".env"
        ).ask()
        has_template = questionary.confirm(
            "Do you use a template file (e.g., .env.example)?",
            default=True
        ).ask()
        template_file = None
        if has_template:
            template_file = questionary.text(
                "What is the name of your template file?",
                default=".env.example"
            ).ask()
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()

    console.print("\n[bold]Generating your `envguard.yml` file...[/bold]")
    config_content = config_manager.generate_default_config(project_name, env_file, template_file)
    config_manager.write_config_file(config_content)

    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print("You can now edit `envguard.yml` to add more complex profiles and links.")
    console.print("Run `envguard list` to see your profiles.")


@app.command(name="list")
def list_profiles_command():
    """Lists all available profiles from your envguard.yml file."""
    try:
        profile_manager.list_profiles()
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

@app.command()
def use(
    profile: str = typer.Argument(..., help="The name of the profile to activate, e.g., 'local-dev'."),
):
    """Switches the active environment to the specified profile."""
    try:
        profile_manager.use_profile(profile)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

@app.command()
def onboard(
    profile: str = typer.Argument(..., help="The name of the profile to set up, e.g., 'local-dev'.")
):
    """
    A guided walkthrough to set up a new environment profile from a template.

    This command is perfect for new developers joining a project. It will:
    1. Create your local config files from the project's templates.
    2. Interactively ask you to fill in the required secrets.
    3. Activate the new environment for you.
    """
    try:
        profile_manager.onboard_profile(profile)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Onboarding cancelled by user.[/yellow]")
        raise typer.Exit()
    except Exception as e:

        raise typer.Exit(code=1)

@app.command()
def scan(
    paths: List[str] = typer.Argument(None, help="Paths to files or directories to scan. Defaults to current directory."),
    staged: bool = typer.Option(False, "--staged", help="Only scan files staged for the next Git commit.")
):
    """
    Scans files for hardcoded secrets.
    """
    console.print("\n[bold cyan]🛡️  Running EnvGuard Secret Scanner...[/bold cyan]")

    files_to_scan = []
    if staged:
        console.print("Scanning [yellow]staged files[/yellow]...")
        files_to_scan = git_utils.get_staged_files()
        if not files_to_scan:
            console.print("[green]No staged files to scan.[/green]")
            raise typer.Exit()
    elif paths:
        for path in paths:
            if os.path.isfile(path):
                files_to_scan.append(os.path.abspath(path))
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in files:
                        files_to_scan.append(os.path.join(root, file))
    else:
        # Default to scanning the current directory recursively
        console.print("Scanning [yellow]current directory[/yellow] recursively...")
        for root, _, files in os.walk("."):
            for file in files:
                files_to_scan.append(os.path.join(root, file))

    all_findings = []
    for file_path in files_to_scan:
        # A simple check to avoid scanning large binary files
        if os.path.exists(file_path) and os.path.getsize(file_path) > 1_000_000: # 1MB limit
            continue
        findings = scanner.scan_file_for_secrets(file_path)
        all_findings.extend(findings)

    if not all_findings:
        console.print("[bold green]✓ No secrets found. You're good to go![/bold green]")
        raise typer.Exit()

    console.print(f"\n[bold red]🚨 DANGER: Found {len(all_findings)} potential secret(s)![/bold red]")

    table = Table(title="Secret Scan Results", border_style="red")
    table.add_column("File", style="cyan")
    table.add_column("Line", style="yellow")
    table.add_column("Secret Type", style="magenta")
    table.add_column("Line Content", style="white")

    for finding in all_findings:
        table.add_row(
            finding["file_path"],
            str(finding["line_num"]),
            finding["secret_type"],
            finding["line_content"]
        )

    console.print(table)
    console.print("\n[bold red]Please remove these secrets from your files before committing.[/bold red]")
    # Exit with a non-zero code to signal failure to other scripts (like the git hook)
    raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
