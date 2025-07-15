import typer
import os
import questionary
from rich.console import Console
from rich.panel import Panel

from .config import manager as config_manager
from .core import profile_manager
from .core.exceptions import EnvGuardException
from .core import template_manager


# Create the main Typer application instance.
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

@template_app.command(name="check")
def template_check(
    profile: str = typer.Option(None, "--profile", "-p", help="The profile to check against the template. Defaults to the active profile."),
):
    """Checks if your environment files are in sync with the template."""
    from envguard import state # Local import to avoid circular dependency

    try:
        # If no profile is specified, use the active profile
        profile = state.get_active_profile()
        if not profile:
            console.print("[red]Error:[/red] No profile specified and no profile is active.")
            console.print("Please specify a profile with `--profile <name>` or run `envguard use <name>` first.")
            raise typer.Exit(code=1)
        console.print(f"Checking active profile: [cyan]{profile}[/cyan]")

        template_manager.check_template(profile)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")

@template_app.command("sync")
def template_sync(
    profile: str = typer.Option(None, "--profile", "-p", help="The profile to sync. Defaults to the active profile.")
):
    """Interactively sync your template file with your source files."""
    console.print(f"Feature coming soon: Interactively syncing template for profile [bold cyan]{profile}[/bold cyan]")

@app.command()
def init():
    """
    Initializes EnvGuard in the current project directory.

    This interactive command will guide you through creating an `envguard.yml`
    configuration file, which is the heart of your project's EnvGuard setup.
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
        env_file = questionary.text(
            "What is your primary environment file for local development?",
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
    console.print("You can now manage your environments using the `envguard` command.")
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
    profile: str = typer.Argument(..., help="The name of the profile to activate, e.g., 'dev'."),
    target: str = typer.Option(".env", "--target", "-t", help="The target file to create as a symlink.")
):
    """Switches the active environment to the specified profile."""
    try:
        profile_manager.use_profile(profile, target_file=target)
    except EnvGuardException as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()