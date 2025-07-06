import typer
import os
import questionary
from rich.console import Console
from rich.panel import Panel

from .config import manager as config_manager
from .core import profile_manager
from .core.exceptions import EnvGuardException

# Create the main Typer application instance.
app = typer.Typer(
    name="envguard",
    help="🛡️  A CLI tool to manage, secure, and collaborate on environment variables.",
    rich_markup_mode="markdown",
    add_completion=False
)

console = Console()

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
    config_content = config_manager.generate_default_config_content(project_name, env_file, template_file)
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