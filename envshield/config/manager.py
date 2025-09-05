import os
from typing import Any, Dict, Optional

import toml
import yaml
from rich.console import Console

from envshield.core.exceptions import ConfigNotFoundError, SchemaNotFoundError

CONFIG_FILE_NAME = "envshield.yml"
SCHEMA_FILE_NAME = "env.schema.toml"
console = Console()


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads and parses the envshield.yml file.
    """
    config_path = path or CONFIG_FILE_NAME
    if not os.path.exists(config_path):
        if path:
            raise ConfigNotFoundError(
                f"Configuration file not found at '{config_path}'"
            )
        return {}
    try:
        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)
            return config_data if config_data else {}
    except (yaml.YAMLError, IOError) as e:
        console.print(f"[bold red]Error:[/bold red] Failed to parse {config_path}: {e}")
        raise


def load_schema() -> Dict[str, Any]:
    """
    Loads and parses the env.schema.toml file.
    """
    if not os.path.exists(SCHEMA_FILE_NAME):
        raise SchemaNotFoundError()
    try:
        with open(SCHEMA_FILE_NAME, "r") as f:
            return toml.load(f)
    except toml.TomlDecodeError as e:
        console.print(
            f"[bold red]Error:[/bold red] Failed to parse {SCHEMA_FILE_NAME}: {e}"
        )
        raise


def generate_default_config_content(project_name: str) -> str:
    """
    Generates the YAML content for a default envshield.yml configuration file.
    """
    config_data = {
        "project_name": project_name,
        "version": 2.0,
        "schema": SCHEMA_FILE_NAME,
        "secret_scanning": {
            "exclude_files": [],
        },
    }
    header = (
        "# EnvShield Configuration File\n"
        "# This file manages your project's security settings.\n\n"
    )
    return header + yaml.dump(config_data, sort_keys=False, indent=2)


def generate_default_schema_content() -> str:
    """
    Generates the TOML content for a default env.schema.toml file.
    """
    return """
    # Welcome to your EnvShield Schema!
    # This file is the single source of truth for all your project's environment variables.
    # The 'check' and 'schema sync' commands are powered by this file.

    [DATABASE_URL]
    description = "The full connection string for the PostgreSQL database."
    secret = true # Marks this as a secret for future scanning and onboarding features.

    [LOG_LEVEL]
    description = "Controls the application's log verbosity (e.g., info, debug)."
    secret = false
    defaultValue = "info"
    """


def write_file(file_name: str, content: str, success_message: str):
    """Generic file writing function."""
    try:
        with open(file_name, "w") as f:
            f.write(content)
        console.print(f"[bold green]✓[/bold green] {success_message}")
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write {file_name}: {e}")
        raise


# def config_file_exists(path: Optional[str] = None) -> bool:
#     """Checks if the configuration file exists in the CWD or at a specific path."""
#     config_path = path or CONFIG_FILE_NAME
#     return os.path.exists(config_path)


# def write_config_file(content: str):
#     """
#     Writes the given content to the envshield.yml file.

#     Args:
#         content: The string content to be written to the file.
#     """
#     try:
#         with open(CONFIG_FILE_NAME, "w") as f:
#             f.write(content)
#         console.print(
#             f"[bold green]✓[/bold green] Successfully created [bold cyan]{CONFIG_FILE_NAME}[/bold cyan]!"
#         )
#     except IOError as e:
#         console.print(f"[bold red]Error:[/bold red] Failed to write config file: {e}")
#         raise
