import os
from typing import Any, Dict, Optional

import yaml
from rich.console import Console

from envshield.core.exceptions import ConfigNotFoundError

CONFIG_FILE_NAME = "envshield.yml"
console = Console()


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads and parses the envshield.yml file.

    It will load from the specified path if provided, otherwise it looks
    for envshield.yml in the current directory.

    Args:
        path: An optional path to a specific config file.

    Raises:
        ConfigNotFoundError: If the configuration file does not exist.

    Returns:
        A dictionary representing the parsed YAML configuration.
    """
    config_path = path or CONFIG_FILE_NAME

    if not os.path.exists(config_path):
        # Only raise an error if a specific path was given.
        # It's okay for the default file to not exist for some commands.
        if path:
            raise ConfigNotFoundError(f"Configuration file not found at '{config_path}'")
        # If no path was given and the default doesn't exist, return empty config
        return {}

    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
            return config_data if config_data else {}
    except yaml.YAMLError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to parse {config_path}: {e}")
        raise
    except IOError as e:
        raise ConfigNotFoundError(f"Could not read config file at '{config_path}': {e}")


def generate_default_config_content(
    project_name: str, env_file: str, template_file: Optional[str] = None
) -> str:
    """
    Generates the YAML content for a default envshield.yml configuration file.

    Args:
        project_name: The name of the project, provided by the user.
        env_file: The path to the main environment file (e.g. .env).
        template_file: The path to the environment template file (optional).

    Returns:
        A string containing the YAML configuration.
    """
    config_data = {
        "project_name": project_name,
        "version": 1.3,
        "profiles": {
            "dev": {
                "description": "Default profile for local development.",
                "links": [{"source": env_file, "target": ".env", "template": template_file}],
            }
        },
        "secret_scanning": {
            "exclude_files": [],
        },
    }
    if not template_file:
        del config_data["profiles"]["dev"]["links"][0]["template"]

    header = (
        "# EnvShield Configuration File\n"
        "# This file manages your environment profiles, security settings, and more.\n\n"
    )
    return header + yaml.dump(config_data, sort_keys=False, indent=2)


def config_file_exists(path: Optional[str] = None) -> bool:
    """Checks if the configuration file exists in the CWD or at a specific path."""
    config_path = path or CONFIG_FILE_NAME
    return os.path.exists(config_path)


def write_config_file(content: str):
    """
    Writes the given content to the envshield.yml file.

    Args:
        content: The string content to be written to the file.
    """
    try:
        with open(CONFIG_FILE_NAME, "w") as f:
            f.write(content)
        console.print(
            f"[bold green]✓[/bold green] Successfully created [bold cyan]{CONFIG_FILE_NAME}[/bold cyan]!"
        )
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write config file: {e}")
        raise
