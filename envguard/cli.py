import typer
import os
import questionary
from rich.console import Console
from rich.panel import Panel

from .config import manager

# Create a typer application instance
app = typer.Typer(
    name="envguard",
    help="A CLI tool to manage, secure, and collaborate on environment variables.",
    rich_markup_mode="markdown"
)
console = Console()

@app.command()
def init():
    """
    Initializes EnvGuard in the current project directory.
    This command will guide you through creating an `envguard.yml` file
    """
    console.print(Panel(
        "[bold cyan]Welcome to EnvGuard Initialization![/bold cyan]\n\nThis wizard will help you set up your project.",
        title="EnvGuard",
        border_style="green"
    ))
    # Check if config file already exists
    if manager.config_exists():
        overwrite = questionary.confirm(
            "An `envguard.yml` file already exists. Do you want to overwrite it?",
            default=False
        ).ask()
        if not overwrite:
            console.print("[bold yellow]Initialization aborted. No changes made.[/bold yellow]")
            raise typer.Exit()

    # ---- Interactive prompts for project details ----
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
            "Do you use a template file for your environment variables? (e.g., .env.template)",
            default=True
        ).ask()

        template_file = None
        if has_template:
            template_file = questionary.text(
                "What is the name of your template file?",
                default=".env.template"
            ).ask()

    except KeyboardInterrupt:
        console.print("[bold red]Initialization cancelled by user.[/bold red]")
        raise typer.Exit()


    # Generate and write the config file
    console.print("\n[bold]Generating your `envguard.yml` file...[/bold]")
    config_content = manager.generate_default_config(project_name, env_file, template_file)

    manager.write_config_file(config_content)

    console.print("\n[bold green]Setup Complete![/bold green]")
    console.print("You can now manage your environments using the `envguard` command.")
    console.print("Try `envguard --help` to see available commands.")

# This is a placeholder for future commands
@app.command()
def use(profile: str = typer.Argument(..., help="The profile to activate, e.g., 'dev' or 'prod'.")):
    """Switches the active environment to the specified profile."""
    console.print(f"Feature coming soon: Switching to profile [bold cyan]{profile}[/bold cyan]")

if __name__ == "__main__":
    app()