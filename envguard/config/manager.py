import os
from typing import Any, Dict

import yaml
from rich.console import Console

# Import our custom exceptions
from envguard.core.exceptions import ConfigNotFoundError

# Define the name for the configuration file
CONFIG_FILE_NAME = "envguard.yml"

console = Console()


def load_config() -> Dict[str, Any]:
    """
    Loads and parses the envguard.yml file from the current directory.

    Raises:
        ConfigNotFoundError: If the envguard.yml file does not exist.

    Returns:
        A dictionary representing the parsed YAML configuration
    """
    if not config_file_exists():
        raise ConfigNotFoundError()

    try:
        with open(CONFIG_FILE_NAME, "r") as f:
            config_data = yaml.safe_load(f)
            if not config_data:
                console.print("[bold yellow]Warning:[/bold yellow] The configuration file is empty.")
                return {}
            return config_data
    except yaml.YAMLError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to parse {CONFIG_FILE_NAME}: {e}")
        raise

def generate_default_config(project_name: str, env_file: str, template_file: str|None = None) -> str:
    """
    Generates the YAML content for a default envguard.yml configuration file.

    Args:
        project_name: The name of the project, provided by the user.
        env_file: The path to the main environment file (e.g. .env).
        template_file: The path to the environment template file (optional).

    Returns:
        A string containing the YAML configuration.
    """
    config_data = {
        "project_name": project_name,
        "version": 1,
        "profiles": {
            "dev": {
                "description": "Default profile for local development",
                "source": env_file,
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

    if template_file:
        config_data["profiles"]["dev"]["template"] = template_file

    # Add comments to guide the user
    header = (
        "# EnvGuard Configuration File\n"
        "# This file manages your environment profiles, security settings, and more.\n"
        "# For more information, visit: https://github.com/rabbilyasar/envguard\n\n"
    )

    return header + yaml.dump(config_data, sort_keys=False, indent=2)

def config_file_exists() -> bool:
    """Checks if the envguard.yml configuration file already exists in the CWD."""
    return os.path.exists(CONFIG_FILE_NAME)

def write_config_file(content: str):
    """
    Writes the given content to the envguard.yml file.

    Args:
        content: The string content to be written to the file.
    """
    try:
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(content)
        console.print(f"[bold green]✓[/bold green] Successfully created [bold cyan]{CONFIG_FILE_NAME}[/bold cyan]!")
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write config file: {e}")
        raise
