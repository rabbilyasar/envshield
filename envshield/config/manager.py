import os
from typing import Any, Dict, Optional

import toml
import yaml
from rich.console import Console

from envshield.core.exceptions import ConfigNotFoundError, SchemaNotFoundError

CONFIG_FILE_NAME = "envshield.yml"
SCHEMA_FILE_NAME = "env.schema.toml"
GITIGNORE_FILE_NAME = ".gitignore"
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
            "exclude_files": [
                "**/tests/*",
                "**/test/*",
            ],
        },
    }
    header = (
        "# EnvShield Configuration File\n"
        "# This file manages your project's security settings.\n\n"
    )
    return header + yaml.dump(config_data, sort_keys=False, indent=2)


def get_framework_schema(project_type: Optional[str]) -> dict:
    """Returns a dictionary of common variables for a given framework."""
    if project_type == "nextjs":
        return {
            "DATABASE_URL": {
                "description": "Database connection string.",
                "secret": True,
            },
            "NEXTAUTH_SECRET": {
                "description": "A secret for NextAuth session signing.",
                "secret": True,
            },
            "NEXT_PUBLIC_API_URL": {
                "description": "Public URL for the frontend to call the API.",
                "secret": False,
            },
        }

    if project_type == "python-django":
        return {
            "SECRET_KEY": {
                "description": "Django's secret key for cryptographic signing.",
                "secret": True,
            },
            "DEBUG": {
                "description": "Django's debug mode.",
                "secret": False,
                "defaultValue": "True",
            },
            "DATABASE_URL": {
                "description": "Database connection string (e.g., dj-database-url).",
                "secret": True,
            },
            "ALLOWED_HOSTS": {
                "description": "A comma-separated list of allowed hostnames.",
                "secret": False,
                "defaultValue": "localhost,127.0.0.1",
            },
        }

    if project_type == "python-flask" or project_type == "python":
        return {
            "SECRET_KEY": {
                "description": "Flask's secret key for signing sessions.",
                "secret": True,
            },
            "FLASK_ENV": {
                "description": "The environment for Flask (e.g., development, production).",
                "secret": False,
                "defaultValue": "development",
            },
            "DATABASE_URL": {
                "description": "Database connection string.",
                "secret": True,
            },
        }

    # Default for all other project types
    return {
        "DATABASE_URL": {
            "description": "The full connection string for the database.",
            "secret": True,
        },
        "LOG_LEVEL": {
            "description": "Controls the log verbosity.",
            "secret": False,
            "defaultValue": "info",
        },
    }


def generate_default_schema_content(project_type: Optional[str]) -> str:
    """Generates TOML content for a default env.schema.toml."""
    header = (
        "# Welcome to your EnvShield Schema!\n"
        "# This is the single source of truth for your project's environment variables.\n\n"
        "# The 'secret' flag marks a variable as sensitive.\n"
        "# In future versions, commands like 'onboard' will use this flag to know\n"
        "# which variables to securely prompt for.\n\n"
    )

    schema_dict = get_framework_schema(project_type)
    return header + toml.dumps(schema_dict)


def update_gitignore():
    """Appends EnvShield patterns to the project's .gitignore file if they don't exist."""
    patterns_to_add = [
        "\n# EnvShield Files\n",
        ".env.local",
        ".env.*.local",
        ".envshield/",
    ]

    try:
        existing_content = ""
        if os.path.exists(GITIGNORE_FILE_NAME):
            with open(GITIGNORE_FILE_NAME, "r") as f:
                existing_content = f.read()

        # Use a flag to avoid adding the header if any pattern already exists
        header_needed = True
        for pattern in patterns_to_add:
            if pattern.strip() in existing_content:
                header_needed = False
                break

        if not header_needed:
            console.print(
                f"[dim]'{GITIGNORE_FILE_NAME}' already contains EnvShield patterns. Skipping.[/dim]"
            )
            return

        with open(GITIGNORE_FILE_NAME, "a") as f:
            for pattern in patterns_to_add:
                f.write(pattern + "\n")

        console.print(
            f"[bold green]✓[/bold green] Updated [bold cyan]{GITIGNORE_FILE_NAME}[/bold cyan] with EnvShield patterns."
        )
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Could not update .gitignore: {e}")


def write_file(file_name: str, content: str, success_message: str):
    """Generic file writing function."""
    try:
        with open(file_name, "w") as f:
            f.write(content)
        console.print(f"[bold green]✓[/bold green] {success_message}")
    except IOError as e:
        console.print(f"[bold red]Error:[/bold red] Failed to write {file_name}: {e}")
        raise
