# envguard/cli.py
import typer
import os
import questionary
import stat
import fnmatch
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from typing import List

from .config import manager as config_manager
from .core import profile_manager, template_manager, scanner
from .core.exceptions import EnvGuardException
from .utils import git_utils

app = typer.Typer(
    name="envguard",
    help="🛡️  A CLI tool to manage, secure, and collaborate on environment variables.",
    rich_markup_mode="markdown",
    add_completion=False
)

console = Console()

# A hardcoded list of default patterns to exclude from scans.
DEFAULT_EXCLUDE_PATTERNS = [
    ".git/*",
    "node_modules/*",
    "vendor/*",
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg",
    "*.woff", "*.woff2",
    "*.bin",
]

# --- Main Commands ---
@app.command()
def init():
    """
    Initializes EnvGuard in the current project directory.
    """
    console.print(Panel("[bold cyan]Welcome to EnvGuard Initialization![/bold cyan]\n\nThis wizard will help you set up your project.", title="🛡️ EnvGuard", border_style="green"))
    if config_manager.config_file_exists():
        overwrite = questionary.confirm("An `envguard.yml` file already exists. Do you want to overwrite it?", default=False).ask()
        if not overwrite:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit()
    try:
        project_name = questionary.text("What is the name of your project?", default=os.path.basename(os.getcwd())).ask()
        env_file = questionary.text("What is your primary environment file for local development?", default=".env").ask()
        has_template = questionary.confirm("Do you use a template file (e.g., .env.example)?", default=True).ask()
        template_file = None
        if has_template:
            template_file = questionary.text("What is the name of your template file?", default=".env.example").ask()
    except (KeyboardInterrupt, TypeError):
        console.print("\n[yellow]Initialization cancelled by user.[/yellow]")
        raise typer.Exit()
    console.print("\n[bold]Generating your `envguard.yml` file...[/bold]")
    config_content = config_manager.generate_default_config_content(project_name, env_file, template_file)
    config_manager.write_config_file(config_content)
    console.print("\n[bold green]✨ Setup Complete! ✨[/bold green]")
    console.print("Run `envguard list` to see your profiles.")

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

# --- Template Subcommand ---
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

# --- Top-level commands for scanning ---
@app.command()
def scan(
    paths: List[str] = typer.Argument(None, help="Paths to files or directories to scan. Defaults to current directory."),
    staged: bool = typer.Option(False, "--staged", help="Only scan files staged for the next Git commit.")
):
    """Scans files for hardcoded secrets."""
    console.print("\n[bold cyan]🛡️  Running EnvGuard Secret Scanner...[/bold cyan]")

    # Start with the built-in defaults.
    exclude_patterns = DEFAULT_EXCLUDE_PATTERNS.copy()
    try:
        config = config_manager.load_config()
        scan_config = config.get("secret_scanning", {})
        # Add any user-defined rules.
        exclude_patterns.extend(scan_config.get("exclude_files", []))
    except EnvGuardException:
        # If no config file, just use the defaults.
        pass

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
        console.print("Scanning [yellow]current directory[/yellow] recursively...")
        for root, _, files in os.walk("."):
            for file in files:
                files_to_scan.append(os.path.join(root, file))

    # Filter the file list based on the combined exclusion rules.
    final_files_to_scan = []
    for file_path in files_to_scan:
        is_excluded = False
        # Normalize path for consistent matching
        normalized_path = file_path.replace(os.getcwd() + os.sep, "")
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(normalized_path, pattern):
                is_excluded = True
                break
        if not is_excluded:
            final_files_to_scan.append(file_path)

    all_findings = []
    for file_path in final_files_to_scan:
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
    raise typer.Exit(code=1)

@app.command("install-hook")
def install_hook():
    """
    Installs the Git pre-commit hook to scan for secrets automatically.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        console.print("[red]Error:[/red] Not inside a Git repository. Cannot install hook.")
        raise typer.Exit(code=1)

    hooks_dir = os.path.join(git_root, ".git", "hooks")
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")

    # The content of the script we want to write.
    hook_script_content = "#!/bin/sh\n\n# Hook installed by EnvGuard\nenvguard scan --staged\n"

    try:
        if os.path.exists(pre_commit_path):
            overwrite = questionary.confirm(
                "A pre-commit hook already exists. Do you want to overwrite it?",
                default=False
            ).ask()
            if not overwrite:
                console.print("[yellow]Hook installation cancelled.[/yellow]")
                raise typer.Exit()

        with open(pre_commit_path, "w") as f:
            f.write(hook_script_content)

        # Make the hook executable (crucial step)
        # stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH makes it executable for everyone
        os.chmod(pre_commit_path, stat.S_IMODE(os.stat(pre_commit_path).st_mode) | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        console.print("[bold green]✓ Git pre-commit hook installed successfully![/bold green]")
        console.print("EnvGuard will now automatically scan for secrets before every commit.")

    except (IOError, OSError) as e:
        console.print(f"[red]Error:[/red] Failed to write or set permissions for the hook file: {e}")
        raise typer.Exit(code=1)
    except (TypeError): # Catches questionary cancellation
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()


if __name__ == "__main__":
    app()
