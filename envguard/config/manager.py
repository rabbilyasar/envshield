import yaml
import os
from rich.console import Console

# Define the name for the configuration file
CONFIG_FILE_NAME = "envguard.yml"

console = Console()

def generate_default_config(project_name: str, env_file: str, template_file: str = None) -> str:
    """
    Generates the content for a default envguard.yml configuration file.

    Args:
        project_name: The name of the project.
        env_file: The path to the main environment file.
        template_file: The path to the environment template file (optional).

    Returns:
        A string containing the YAML configuration.
    """
    config_data = {
        "project_name": project_name,
        "version": 1,
        "profiles": {
            "dev": {
                "source": env_file,
                "template": template_file
            }
        },
        "schema_validation": {
            # Example:
            # "DATABASE_URL": {"type": "string", "required": True},
            # "PORT": {"type": "integer", "required": False}
        },
        "secret_scanning": {
            "exclude_files": [".git/*", "node_modules/*", "*.enc"],
            "exclude_patterns": []
        }
    }

    # Remove template key if not provided
    if not template_file:
        del config_data["profiles"]["dev"]["template"]

    # Add comments to guide the user
    header = (
        "# EnvGuard Configuration File\n"
        "# This file manages your environment profiles, security settings, and more.\n"
        "# For more information, visit: https://github.com/rabbilyasar/envguard\n\n"
    )

    return header + yaml.dump(config_data, sort_keys=False, indent=2)

def config_exists() -> bool:
    """Checks if the envguard.yml configuration file already exists."""
    return os.path.exists(CONFIG_FILE_NAME)

def write_config_file(content: str):
    """
    Writes the given content to the envguard.yml file.
    """
    try:
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(content)
        console.print(f"[bold green]✓[/bold green] Successfully created [cyan]{CONFIG_FILE_NAME}[/cyan]!")
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write config file: {e}")
        raise
