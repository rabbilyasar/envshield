# envshield/core/schema_manager.py
import datetime
import os
from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table

from envshield.config import manager as config_manager
from envshield.core import file_updater, schema_types
from envshield.core.exceptions import EnvShieldException
from envshield.parsers.factory import get_parser

console = Console()


class SchemaDiff:
    """
    The pure result of comparing a schema against a set of real local
    values -- no I/O, no printing. `check_schema` renders this as a table;
    `doctor`'s health checks render it as a one-line summary. Having one
    shared computation means the two can never quietly disagree about what
    counts as missing/blank/invalid/extra.
    """

    def __init__(
        self,
        missing: set,
        blank: set,
        invalid: Dict[str, str],
        extra: set,
    ):
        self.missing = missing
        self.blank = blank
        self.invalid = invalid
        self.extra = extra

    @property
    def is_clean(self) -> bool:
        return not (self.missing or self.blank or self.invalid or self.extra)

    def summary(self) -> str:
        messages = []
        if self.missing:
            messages.append(f"Missing variables: {', '.join(sorted(self.missing))}")
        if self.blank:
            messages.append(f"Required but blank: {', '.join(sorted(self.blank))}")
        if self.invalid:
            details_str = "; ".join(f"{k} ({v})" for k, v in sorted(self.invalid.items()))
            messages.append(f"Invalid values: {details_str}")
        if self.extra:
            messages.append(f"Extra variables: {', '.join(sorted(self.extra))}")
        return "; ".join(messages)


def diff_against_schema(schema: Dict[str, Any], local_values: Dict[str, str]) -> SchemaDiff:
    """
    Compares `local_values` (as read from a .env file, a deployment
    manifest, or anything else a parser can produce) against `schema`.

    A variable is required if it has no defaultValue, and (when declared)
    its 'requiredIf' condition currently holds against the other local
    values -- see schema_types.is_required_now. A required var declared
    only as a blank placeholder (e.g. `SECRETS_ENCRYPTION_KEY = ""` checked
    in ahead of a real per-developer secret) is reported distinctly from
    one missing outright. A present, non-blank value still needs to match
    its declared type/enum/pattern.
    """
    local_vars = set(local_values.keys())
    schema_vars_all = set(schema.keys())

    schema_vars_required = {
        key
        for key, details in schema.items()
        if schema_types.is_required_now(details, local_values)
    }

    missing = set()
    blank = set()
    for key in schema_vars_required:
        if key not in local_values:
            missing.add(key)
        elif not local_values[key]:
            blank.add(key)

    invalid: Dict[str, str] = {}
    for key, value in local_values.items():
        if key in schema and value:
            error = schema_types.validate_value(value, schema[key])
            if error:
                invalid[key] = error

    extra = local_vars - schema_vars_all

    return SchemaDiff(missing=missing, blank=blank, invalid=invalid, extra=extra)


def check_schema(
    file_path: str,
    service_name: Optional[str] = None,
    container: Optional[str] = None,
) -> bool:
    """
    Validates a local environment file against the env.schema.toml,
    intelligently handling variables with default values.

    If `service_name` is provided, validates against that service's schema.
    Otherwise, validates against the root schema.

    `file_path` can also be a docker-compose or Kubernetes manifest -- see
    parsers/_docker_compose.py and parsers/_kubernetes.py. `container`
    picks which service/container to check when the manifest declares more
    than one; if omitted, `service_name` is tried as a same-named fallback
    before giving up and asking for it explicitly (see the parsers' `prefer`).

    Returns:
        True if the local file is in sync with the schema, False otherwise
        (including when the file or a usable parser can't be found).
    """
    console.print(
        f"\n[bold]Validating [magenta]{file_path}[/magenta] against schema...[/bold]"
    )

    # Load the schema and the local .env file
    schema = config_manager.load_schema(service_name=service_name)
    parser = get_parser(file_path, container=container, prefer=service_name)

    if not parser:
        console.print(f"[red]Error:[/red] No parser found for file type '{file_path}'.")
        return False

    try:
        local_values = parser.get_vars(file_path, get_values=True)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] File not found: '{file_path}'.")
        return False
    except (ValueError, EnvShieldException) as e:
        console.print(f"[red]Error:[/red] {e}")
        return False

    diff = diff_against_schema(schema, local_values)

    if diff.is_clean:
        console.print(
            "[bold green]✓ Your configuration is perfectly in sync with the schema![/bold green]"
        )
        return True

    # If there are issues, build and display a report table
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Status", style="cyan")
    table.add_column("Variable Name", style="white")
    table.add_column("Source", style="white")

    for var in sorted(diff.missing):
        table.add_row("[red]Missing in Local[/red]", var, "env.schema.toml (Required)")

    for var in sorted(diff.blank):
        table.add_row("[red]Blank in Local[/red]", var, "env.schema.toml (Required)")

    for var, reason in sorted(diff.invalid.items()):
        table.add_row("[red]Invalid Value[/red]", var, reason)

    for var in sorted(diff.extra):
        table.add_row("[yellow]Extra in Local[/yellow]", var, file_path)

    console.print(table)
    console.print(
        "\n[bold]Suggestion:[/bold] Please update your local file to match the schema contract."
    )
    return False


def sync_schema(service_name: Optional[str] = None):
    """
    Keeps a service's tracked environment template in sync with its schema.

    Dotenv projects (the default) get '.env.example' fully regenerated from
    the schema -- it's a pure generated artifact, safe to overwrite wholesale.

    Projects whose local config is a Python module (`local_file` ends in
    '.py' -- e.g. zeus's `env_config.local.py`) have no such artifact: that
    file IS the hand-maintained contract, often with logic beyond simple
    assignments. So instead of overwriting it, this only appends whatever
    schema variables are missing from it; existing lines, values, and
    surrounding code are left untouched.

    If `service_name` is provided, syncs that service's schema and template
    (resolved to that service's own directory, see config_manager.get_env_paths).
    Otherwise, syncs the root project's schema and '.env.example'.
    """
    schema = config_manager.load_schema(service_name=service_name)
    paths = config_manager.get_env_paths(service_name=service_name)

    if paths["local_file"].endswith(".py"):
        _sync_python_local_file(schema, paths["local_file"])
        return

    output_file = paths["example_file"]
    console.print(f"\n[bold]Generating [cyan]{output_file}[/cyan] from schema...[/bold]")

    header = (
        f"# This file was auto-generated by EnvShield on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# It is generated from the contract defined in env.schema.toml.\n"
        f"# DO NOT EDIT THIS FILE MANUALLY.\n\n"
    )

    content = header
    for key, details in schema.items():
        description = details.get("description")
        if description:
            content += f"# {description}\n"

        default_value = details.get("defaultValue", "")
        content += f"{key}={default_value}\n\n"

    try:
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_file, "w") as f:
            f.write(content)
        console.print(
            f"[bold green]✓[/bold green] Successfully created/updated [bold cyan]{output_file}[/bold cyan]!"
        )
    except IOError as e:
        console.print(f"[red]Error:[/red] Could not write to {output_file}: {e}")


def _sync_python_local_file(schema: Dict[str, Any], local_file: str) -> None:
    """Ensures a Python-module local config file declares every schema variable."""
    if not os.path.exists(local_file):
        console.print(f"\n[bold]Creating [cyan]{local_file}[/cyan] from schema...[/bold]")
        lines = [
            "# Auto-generated by 'envshield schema sync'.\n",
            "# Fill in real values below -- this file is your project's local config module.\n\n",
        ]
        for key, details in schema.items():
            description = details.get("description")
            if description:
                lines.append(f"# {description}\n")
            lines.append(f"{key} = {str(details.get('defaultValue', ''))!r}\n\n")

        output_dir = os.path.dirname(local_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(local_file, "w") as f:
            f.writelines(lines)
        console.print(f"[bold green]✓[/bold green] Created [bold cyan]{local_file}[/bold cyan]!")
        return

    console.print(f"\n[bold]Checking [cyan]{local_file}[/cyan] for missing variables...[/bold]")
    parser = get_parser(local_file)
    existing_vars = parser.get_vars(local_file) if parser else set()
    missing = {key: details for key, details in schema.items() if key not in existing_vars}

    if not missing:
        console.print(
            f"[bold green]✓[/bold green] [cyan]{local_file}[/cyan] already declares every schema variable."
        )
        return

    updates = [
        {"key": key, "value": str(details.get("defaultValue", ""))}
        for key, details in missing.items()
    ]
    file_updater.update_variables_in_file(local_file, updates)
    console.print(
        f"[bold green]✓[/bold green] Added {len(updates)} missing variable(s) to "
        f"[bold cyan]{local_file}[/bold cyan]: {', '.join(missing.keys())}"
    )
