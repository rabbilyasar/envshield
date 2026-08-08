import os
from typing import Any, Dict, List, Optional

import toml
import yaml
from rich.console import Console

from envshield.core.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    SchemaNotFoundError,
    SchemaParseError,
    UnsafePathError,
)

CONFIG_FILE_NAME = "envshield.yml"
SCHEMA_FILE_NAME = "env.schema.toml"
GITIGNORE_FILE_NAME = ".gitignore"
console = Console()


def _ensure_within_project(path: str, label: str) -> str:
    """
    Validates that `path` -- typically a service's schema/local_file/
    example_file entry read from envshield.yml -- stays within the current
    project directory, and returns it unchanged if so.

    Raises UnsafePathError otherwise. See UnsafePathError's docstring for
    why this matters: envshield.yml is committed to the repo, so an
    unvalidated override is a supply-chain-style arbitrary read/write vector
    for anyone who clones the repo and runs ordinary commands.
    """
    project_root = os.path.abspath(os.getcwd())
    candidate = os.path.abspath(os.path.join(project_root, path))
    try:
        is_within = os.path.commonpath([project_root, candidate]) == project_root
    except ValueError:
        # Raised on Windows when the two paths are on different drives --
        # definitionally not "within" the project.
        is_within = False
    if not is_within:
        raise UnsafePathError(label, path, project_root)
    return path


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
    except yaml.YAMLError as e:
        raise ConfigParseError(config_path, str(e))
    except IOError as e:
        raise ConfigParseError(config_path, str(e))


def load_schema(service_name: str) -> Dict[str, Any]:
    """
    Loads and parses a service's env.schema.toml file.

    envshield.yml is "the brain": every project, single-service or not,
    always has at least one named entry under `services` (see
    generate_default_config_content) -- there is no separate root/rootless
    shape to special-case here anymore. The schema itself stays "the
    documentation": types, descriptions, secret flags, and everything else
    describing what a variable *means* lives only in the TOML file this
    loads, never in envshield.yml.

    A schema can also declare a top-level `extends` key (a path, or a list
    of paths, to one or more base schemas) to share common variables across
    services without copy-pasting them -- see `_load_schema_file`.
    """
    schema_path = get_service_schema_path(service_name)
    if not schema_path:
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in configuration."
        )

    if not os.path.exists(schema_path):
        raise SchemaNotFoundError(f"Schema file not found: {schema_path}")
    return _load_schema_file(schema_path)


def load_bare_schema(path: str = SCHEMA_FILE_NAME) -> Dict[str, Any]:
    """
    Loads a schema file directly by path, with no service registration
    involved at all.

    For a project that hasn't been registered in envshield.yml yet --
    stateless, read-only commands like 'generate' (and 'import', writing a
    fresh schema for the first time) have no real need for the "brain" at
    all: they don't touch a service's local files, so there's no directory
    or path resolution that actually depends on registration existing.
    Requiring it anyway would just be ceremony for ceremony's sake. This is
    the escape hatch for exactly that case -- callers that DO have a
    registered service should still go through load_schema, which is what
    keeps 'check'/'doctor'/'setup' safe from the single-service orphaning
    bug this file's `services`-always-populated design exists to prevent.
    """
    if not os.path.exists(path):
        raise SchemaNotFoundError(f"Schema file not found: {path}")
    return _load_schema_file(path)


def _load_toml_schema(schema_path: str) -> Dict[str, Any]:
    """Reads and parses one TOML schema file, with friendlier error messages on malformed TOML."""
    try:
        with open(schema_path, "r") as f:
            return toml.load(f)
    except toml.TomlDecodeError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            dup_key = (
                error_msg.split("What? ")[1].split(" ")[0]
                if "What? " in error_msg
                else "unknown"
            )
            details = f"Duplicate key found: {dup_key}\nCheck your schema file for duplicate [{dup_key}] definitions"
        else:
            details = (
                error_msg.split("(line")[0].strip()
                if "(line" in error_msg
                else error_msg
            )
        raise SchemaParseError(schema_path, details)


def _load_schema_file(
    schema_path: str, _visited: Optional[frozenset] = None
) -> Dict[str, Any]:
    """
    Loads one schema file, resolving and merging any `extends` base
    schema(s) it declares (a string or list of strings, each a path
    relative to *this* schema file's own directory).

    A variable declared in both a base schema and the schema that extends
    it is fully replaced by the child's own definition -- no per-field
    deep-merge -- and later entries in an `extends` list override earlier
    ones on a conflict, same as the child overrides all of them. This
    keeps the merge predictable: whichever definition is "closest" to the
    schema actually being loaded always wins.

    `extends` paths are validated the same way service overrides in
    envshield.yml are (see _ensure_within_project) -- a schema file is just
    as committed-and-shared as envshield.yml, so an unvalidated `extends`
    would be the same supply-chain-style path-traversal risk.
    """
    visited = _visited or frozenset()
    real_path = os.path.abspath(schema_path)
    if real_path in visited:
        raise SchemaParseError(schema_path, "circular 'extends' chain detected")
    visited = visited | {real_path}

    raw = _load_toml_schema(schema_path)
    extends = raw.pop("extends", None)

    merged: Dict[str, Any] = {}
    if extends:
        base_refs = [extends] if isinstance(extends, str) else list(extends)
        base_dir = os.path.dirname(schema_path) or "."
        for base_ref in base_refs:
            base_path = _ensure_within_project(
                os.path.normpath(os.path.join(base_dir, base_ref)),
                f"'extends' reference in '{schema_path}'",
            )
            if not os.path.exists(base_path):
                raise SchemaNotFoundError(
                    f"'{schema_path}' extends '{base_path}', which doesn't exist."
                )
            merged.update(_load_schema_file(base_path, _visited=visited))

    merged.update(raw)
    return merged


def get_services() -> Dict[str, Dict[str, Any]]:
    """
    Returns the services defined in envshield.yml, or an empty dict if the
    project hasn't been initialized yet.

    Example return value:
    {
        "api": {"schema": "services/api/env.schema.toml", "description": "Backend API"},
        "web": {"schema": "services/web/env.schema.toml"},
    }
    """
    config = load_config()
    services = config.get("services")
    return services if isinstance(services, dict) else {}


def get_service_schema_path(service_name: str) -> Optional[str]:
    """
    Returns the schema path for a given service, or None if the service
    doesn't exist.
    """
    services = get_services()
    if service_name not in services:
        return None
    service_config = services[service_name]
    if isinstance(service_config, dict) and "schema" in service_config:
        return _ensure_within_project(
            service_config["schema"], f"service '{service_name}' schema path"
        )
    return None


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
    existing setup, not just bootstrap a fresh one.

    A deployment manifest is registered separately, via add_manifest -- it
    maps a container name to a service name and isn't owned by any one
    service entry (the common real shape is one compose file naming several
    services at once, and duplicating "which manifest, which container" on
    every one of them is exactly the kind of topology-vs-meaning drift this
    file exists to avoid).

    Note: envshield.yml is rewritten via a full YAML re-serialization, so
    any hand-written comments in an existing file won't survive.
    """
    schema_path = _ensure_within_project(schema_path, f"service '{name}' schema path")
    if local_file:
        local_file = _ensure_within_project(local_file, f"service '{name}' local_file")
    if example_file:
        example_file = _ensure_within_project(
            example_file, f"service '{name}' example_file"
        )

    config = load_config()
    services = config.get("services")
    if not isinstance(services, dict):
        services = {}
    config["services"] = services

    entry: Dict[str, Any] = {"schema": schema_path}
    if description:
        entry["description"] = description
    if local_file:
        entry["local_file"] = local_file
    if example_file:
        entry["example_file"] = example_file
    services[name] = entry

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def remove_service(name: str) -> None:
    """
    De-registers one service from envshield.yml. Never deletes the
    service's own files (schema, local env file, etc.) -- only the
    registration entry, plus any manifest container mappings that pointed
    at it (a mapping to a service that no longer exists is dead weight, not
    a useful record).
    """
    config = load_config()
    services = config.get("services")
    if not isinstance(services, dict) or name not in services:
        raise SchemaNotFoundError(f"Service '{name}' not found in configuration.")
    del services[name]

    manifests = config.get("manifests")
    if isinstance(manifests, list):
        for entry in manifests:
            containers = entry.get("containers")
            if isinstance(containers, dict):
                for container_name in [k for k, v in containers.items() if v == name]:
                    del containers[container_name]
        config["manifests"] = [entry for entry in manifests if entry.get("containers")]

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def add_manifest(file: str, containers: Dict[str, str]) -> None:
    """
    Registers (or extends) one deployment manifest, mapping its container
    names to already-registered service names. Calling this again for the
    same file merges in whatever new container mappings are given, rather
    than replacing the entry outright -- the same "safe to run repeatedly"
    property add_service has.
    """
    file = _ensure_within_project(file, "deployment manifest path")
    config = load_config()
    manifests = config.get("manifests")
    if not isinstance(manifests, list):
        manifests = []
    config["manifests"] = manifests

    for entry in manifests:
        if entry.get("file") == file:
            existing = entry.get("containers")
            entry["containers"] = {
                **(existing if isinstance(existing, dict) else {}),
                **containers,
            }
            break
    else:
        manifests.append({"file": file, "containers": dict(containers)})

    with open(CONFIG_FILE_NAME, "w") as f:
        yaml.dump(config, f, sort_keys=False, indent=2)


def get_deployment_manifests(service_name: str) -> List[Dict[str, Any]]:
    """
    Returns every registered deployment manifest that maps one of its
    containers to `service_name`, as [{"path": ..., "container": ...}, ...].

    A service can legitimately show up in more than one manifest (a local
    docker-compose.yml and a production Kubernetes manifest, say), so this
    returns a list rather than assuming at most one -- callers that only
    ever expect zero-or-one should just take the first element.
    """
    config = load_config()
    manifests = config.get("manifests")
    if not isinstance(manifests, list):
        return []

    results = []
    for entry in manifests:
        file = entry.get("file")
        containers = entry.get("containers")
        if not file or not isinstance(containers, dict):
            continue
        for container_name, mapped_service in containers.items():
            if mapped_service == service_name:
                path = _ensure_within_project(file, f"deployment manifest '{file}'")
                results.append({"path": path, "container": container_name})
    return results


def get_service_dir(service_name: str) -> str:
    """
    Returns the directory a service's schema lives in -- treated throughout
    EnvShield as that service's root (e.g. 'alpha' for a schema at
    'alpha/env.schema.toml'). Raises SchemaNotFoundError if the service
    isn't declared in envshield.yml.
    """
    schema_path = get_service_schema_path(service_name)
    if not schema_path:
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in configuration."
        )
    return os.path.dirname(schema_path) or "."


def get_env_paths(service_name: str) -> Dict[str, str]:
    """
    Resolves the 'template' (tracked, e.g. '.env.example') and 'local' (real,
    per-developer, e.g. '.env') environment file paths for a service.

    These default to '.env.example' / '.env' inside the service's own
    directory (the directory its schema lives in) -- for a single-service
    project, that directory is the project root itself, so this looks
    exactly like EnvShield's original single-project behaviour; nothing
    moves just because the project is technically "a service" now.

    Either can be overridden via 'example_file' / 'local_file' in
    envshield.yml. An override is required whenever the local config isn't a
    dotenv file at all -- e.g. a Python module such as `env_config.local.py`
    in a Flask project. EnvShield picks its reader/writer by the file's
    extension (see parsers.factory.get_parser), so a '.py' override is enough
    to make 'sync' and 'setup' treat it as source code instead of a dotenv
    file: they patch/append plain assignments in place rather than
    regenerating the file wholesale.
    """
    service_dir = get_service_dir(service_name)
    service_config = get_services().get(service_name)
    if not isinstance(service_config, dict):
        service_config = {}

    def _resolve(override_key: str, default_name: str) -> str:
        override = service_config.get(override_key)
        if override:
            return _ensure_within_project(
                override, f"service '{service_name}' {override_key}"
            )
        return (
            default_name
            if service_dir == "."
            else os.path.join(service_dir, default_name)
        )

    return {
        "example_file": _resolve("example_file", ".env.example"),
        "local_file": _resolve("local_file", ".env"),
    }


def generate_default_config_content(
    project_name: str,
    service_name: str,
    schema_path: str = SCHEMA_FILE_NAME,
    deployment_manifest: Optional[str] = None,
    container: Optional[str] = None,
) -> str:
    """
    Generates the YAML content for a default envshield.yml configuration
    file.

    Always registers exactly one service (`service_name`), even for a
    brand-new single-service project -- there is no separate "rootless"
    shape. Growing from one service to several is then just appending
    another entry to the same `services` map, not a structural migration.

    `deployment_manifest`, when given (a docker-compose file auto-detected
    at the project root -- see service_discovery.find_compose_file), is
    registered up front under `manifests` so `doctor`/`check` validate it
    automatically from the very first run, with no separate opt-in step.
    """
    config_data: Dict[str, Any] = {
        "project_name": project_name,
        "services": {
            service_name: {"schema": schema_path},
        },
        "secret_scanning": {
            "exclude_files": [
                "**/tests/*",
                "**/test/*",
            ],
        },
    }
    if deployment_manifest:
        config_data["manifests"] = [
            {
                "file": deployment_manifest,
                "containers": {container or service_name: service_name},
            }
        ]
    header = "# EnvShield Configuration File\n# This file manages your project's security settings.\n\n"
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
