# envshield/cli.py
import fnmatch
import os
import stat
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .config import manager as config_manager
from .core import profile_manager, scanner, template_manager
from .core.exceptions import EnvShieldException
from .utils import git_utils

app = typer.Typer(
    name="envshield",
    help="🛡️  A CLI tool to manage, secure, and collaborate on environment variables.",
    rich_markup_mode="markdown",
    add_completion=False,
)

console = Console()

DEFAULT_EXCLUDE_PATTERNS = [
    ".git/*",
    "node_modules/*",
    "vendor/*",
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.svg",
    "*.woff",
    "*.woff2",
    "*.bin",
    "**/*__pycache__/*",
]


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
            default=".env",
        ).ask()
        has_template = questionary.confirm(
            "Do you use a template file (e.g., .env.example)?", default=True
        ).ask()
        template_file = None
        if has_template:
            template_file = questionary.text(
                "What is the name of your template file?", default=".env.example"
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
    console.print("Run `envshield list` to see your profiles.")


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


template_app = typer.Typer(
    name="template", help="Manage environment templates.", no_args_is_help=True
)
app.add_typer(template_app, name="template")


@template_app.command("check")
def template_check(
    profile: str = typer.Option(
        None, "--profile", "-p", help="The profile to check. Defaults to active."
    ),
):
    """Checks if your environment files are in sync with the template."""
    from envshield import state

    try:
        if not profile:
            profile = state.get_active_profile()
            if not profile:
                console.print(
                    "[red]Error:[/red] No profile specified and no profile is active."
                )
                raise typer.Exit(code=1)
            console.print(f"Checking active profile: [cyan]{profile}[/cyan]")
        template_manager.check_template(profile)
    except EnvShieldException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


@template_app.command("sync")
def template_sync(
    profile: str = typer.Option(
        None, "--profile", "-p", help="The profile to sync. Defaults to active."
    ),
):
    """Interactively add variables from your source files to your template."""
    from envshield import state

    try:
        if not profile:
            profile = state.get_active_profile()
            if not profile:
                console.print(
                    "[red]Error:[/red] No profile specified and no profile is active."
                )
                raise typer.Exit(code=1)
            console.print(
                f"Syncing template for active profile: [cyan]{profile}[/cyan]"
            )
        template_manager.sync_template(profile)
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
        help="A glob pattern of files/directories to exclude from the scan. Can be used multiple times.",
    ),
):
    """Scans files for hardcoded secrets."""
    console.print("\n[bold cyan]🛡️  Running EnvShield Secret Scanner...[/bold cyan]")

    # exclusions = config_manager.DEFAULT_SCANNER_EXCLUSIONS.copy()

    exclude_patterns = DEFAULT_EXCLUDE_PATTERNS.copy()
    try:
        config = config_manager.load_config()
        config_exclusions = config.get("secret_scanning", {}).get("exclude_files") or []
        exclude_patterns.extend(config_exclusions)
    except EnvShieldException:
        pass

    if exclude:
        exclude_patterns.extend(exclude)

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

    final_files_to_scan = []
    for file_path in files_to_scan:
        is_excluded = False
        normalized_path = file_path.replace(os.getcwd() + os.sep, "")
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(normalized_path, pattern):
                is_excluded = True
                break
        if not is_excluded:
            final_files_to_scan.append(file_path)

    all_findings = []

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("Scanning [cyan]{task.description}[/cyan]"),
        console=console,
    ) as progress:
        scan_task = progress.add_task("files...", total=len(final_files_to_scan))
        for file_path in final_files_to_scan:
            # Update the progress bar with the current file name
            progress.update(
                scan_task, description=os.path.basename(file_path), advance=1
            )

            if os.path.exists(file_path) and os.path.getsize(file_path) > 1_000_000:
                continue
            findings = scanner.scan_file_for_secrets(file_path)
            all_findings.extend(findings)

    if not all_findings:
        console.print(
            "\n[bold green]✓ No secrets found. You're good to go![/bold green]"
        )
        raise typer.Exit()

    console.print(
        f"\n[bold red]🚨 DANGER: Found {len(all_findings)} potential secret(s)![/bold red]"
    )

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
            finding["line_content"],
        )
    console.print(table)

    if staged:
        console.print(
            "\n[bold red]Commit aborted. Please remove these secrets from your files before committing.[/bold red]"
        )
    else:
        console.print(
            "\n[bold red]Review the findings above and remove any active secrets from your project's files.[/bold red]"
        )

    raise typer.Exit(code=1)


@app.command("install-hook")
def install_hook():
    """Installs the Git pre-commit hook to scan for secrets automatically."""
    git_root = git_utils.get_git_root()
    if not git_root:
        console.print(
            "[red]Error:[/red] Not inside a Git repository. Cannot install hook."
        )
        raise typer.Exit(code=1)
    hooks_dir = os.path.join(git_root, ".git", "hooks")
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")
    hook_script_content = (
        "#!/bin/sh\n\n# Hook installed by EnvShield\nenvshield scan --staged\n"
    )
    try:
        if os.path.exists(pre_commit_path):
            overwrite = questionary.confirm(
                "A pre-commit hook already exists. Do you want to overwrite it?",
                default=False,
            ).ask()
            if not overwrite:
                console.print("[yellow]Hook installation cancelled.[/yellow]")
                raise typer.Exit()
        with open(pre_commit_path, "w") as f:
            f.write(hook_script_content)
        os.chmod(
            pre_commit_path,
            stat.S_IMODE(os.stat(pre_commit_path).st_mode)
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH,
        )
        console.print(
            "[bold green]✓ Git pre-commit hook installed successfully![/bold green]"
        )
        console.print(
            "EnvShield will now automatically scan for secrets before every commit."
        )
    except (IOError, OSError) as e:
        console.print(
            f"[red]Error:[/red] Failed to write or set permissions for the hook file: {e}"
        )
        raise typer.Exit(code=1)
    except TypeError:
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()


if __name__ == "__main__":
    app()
