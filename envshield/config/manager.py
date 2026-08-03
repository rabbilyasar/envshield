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


def load_schema(service_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Loads and parses the env.schema.toml file.

    If `service_name` is provided, loads the schema for that specific service
    (from envshield.yml's `services` section). Otherwise, loads the root schema.

    This enables multi-service projects: each service has its own contract.
    """
    schema_path = SCHEMA_FILE_NAME
    if service_name:
        schema_path = get_service_schema_path(service_name)
        if not schema_path:
            raise SchemaNotFoundError(
                f"Service '{service_name}' not found in configuration."
            )

    if not os.path.exists(schema_path):
        raise SchemaNotFoundError(f"Schema file not found: {schema_path}")
    try:
        with open(schema_path, "r") as f:
            return toml.load(f)
    except toml.TomlDecodeError as e:
        console.print(
            f"[bold red]Error:[/bold red] Failed to parse {schema_path}: {e}"
        )
        raise


def get_services() -> Dict[str, Dict[str, Any]]:
    """
    Returns the services defined in envshield.yml, or an empty dict if none.

    Example return value:
    {
        "api": {"path": "services/api/env.schema.toml", "description": "Backend API"},
        "web": {"path": "services/web/env.schema.toml", "description": "Frontend"},
    }
    """
    config = load_config()
    return config.get("services", {})


def get_service_schema_path(service_name: str) -> Optional[str]:
    """
    Returns the schema path for a given service, or None if the service
    doesn't exist.
    """
    services = get_services()
    if service_name not in services:
        return None
    service_config = services[service_name]
    if isinstance(service_config, dict) and "path" in service_config:
        return service_config["path"]
    return None


def is_multi_service() -> bool:
    """Returns True if the project has multiple services configured."""
    return len(get_services()) > 0


def add_service(
    name: str,
    schema_path: str,
    local_file: Optional[str] = None,
    example_file: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """
    Adds (or overwrites) one service entry in envshield.yml, creating the
    file if it doesn't exist yet. Every other top-level key and every other
    already-configured service is left untouched -- this is what makes
    `envshield service discover`/`add` safe to run repeatedly to extend an
    existing multi-service setup, not just bootstrap a fresh one.

    Note: envshield.yml is rewritten via a full YAML re-serialization, so
    any hand-written comments in an existing file won't survive.
    """
    config = load_config()
    services = config.get("services")
    if not isinstance(services, dict):
        services = {}
    config["services"] = services

    entry: Dict[str, Any] = {"path": schema_path}
    if description:
        entry["description"] = description
    if local_file:
        entry["local_file"] = local_file
    if example_file:
        entry["example_file"] = example_file
    services[name] = entry

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def get_service_dir(service_name: str) -> str:
    """
    Returns the directory a service's schema lives in -- treated throughout
    EnvShield as that service's root (e.g. 'athena' for a schema at
    'athena/env.schema.toml'). Raises SchemaNotFoundError if the service
    isn't declared in envshield.yml.
    """
    schema_path = get_service_schema_path(service_name)
    if not schema_path:
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in configuration."
        )
    return os.path.dirname(schema_path) or "."


def get_env_paths(service_name: Optional[str] = None) -> Dict[str, str]:
    """
    Resolves the 'template' (tracked, e.g. '.env.example') and 'local' (real,
    per-developer, e.g. '.env') environment file paths for a project or service.

    For a single-service project these default to '.env.example' / '.env' in
    the current directory -- EnvShield's original behaviour. For a service
    declared in envshield.yml, they default to the same filenames inside that
    service's own directory (the directory its schema lives in), so sibling
    services in a monorepo don't collide on one shared root-level file.

    Either can be overridden per-service via 'example_file' / 'local_file' in
    envshield.yml. An override is required whenever the local config isn't a
    dotenv file at all -- e.g. a Python module such as `env_config.local.py`
    in a Flask project. EnvShield picks its reader/writer by the file's
    extension (see parsers.factory.get_parser), so a '.py' override is enough
    to make 'schema sync' and 'setup' treat it as source code instead of a
    dotenv file: they patch/append plain assignments in place rather than
    regenerating the file wholesale.
    """
    service_dir = "."
    service_config: Dict[str, Any] = {}

    if service_name:
        service_dir = get_service_dir(service_name)
        raw_config = get_services().get(service_name)
        if isinstance(raw_config, dict):
            service_config = raw_config

    def _resolve(override_key: str, default_name: str) -> str:
        override = service_config.get(override_key)
        if override:
            return override
        return (
            default_name
            if service_dir == "."
            else os.path.join(service_dir, default_name)
        )

    return {
        "example_file": _resolve("example_file", ".env.example"),
        "local_file": _resolve("local_file", ".env"),
    }


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
    # '.env' comes first and matters most: it's the actual secrets file every
    # other command assumes is never committed. The '.local'/'.envshield'
    # variants are for per-developer overrides and EnvShield's own state.
    patterns_to_add = [
        ".env",
        ".env.local",
        ".env.*.local",
        ".envshield/",
    ]

    try:
        existing_content = ""
        if os.path.exists(GITIGNORE_FILE_NAME):
            with open(GITIGNORE_FILE_NAME, "r") as f:
                existing_content = f.read()

        # Check each pattern independently -- a project that already has
        # '.env.local' ignored (e.g. from an older EnvShield version) should
        # still get '.env' added, not have the whole update skipped.
        existing_lines = {line.strip() for line in existing_content.splitlines()}
        missing_patterns = [p for p in patterns_to_add if p not in existing_lines]

        if not missing_patterns:
            console.print(
                f"[dim]'{GITIGNORE_FILE_NAME}' already contains EnvShield patterns. Skipping.[/dim]"
            )
            return

        with open(GITIGNORE_FILE_NAME, "a") as f:
            f.write("\n# EnvShield Files\n")
            for pattern in missing_patterns:
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
