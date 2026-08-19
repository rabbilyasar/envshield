# envshield/core/schema_manager.py
import datetime
import os
from typing import Any, Dict, Optional

from rich.console import Console
from rich.table import Table

from envshield.config import manager as config_manager
from envshield.core import file_updater, schema_types
from envshield.core.exceptions import EnvShieldException
from envshield.parsers._base import BaseParser
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
        unresolved: Optional[set] = None,
    ):
        self.missing = missing
        self.blank = blank
        self.invalid = invalid
        self.extra = extra
        # Required variables this source's own unresolved envFrom-style
        # reference *might* supply -- distinct from `missing` (which means
        # EnvShield positively knows the variable isn't there). Counted
        # against is_clean for the same reason `missing` is: EnvShield
        # cannot confirm the variable is satisfied, so it must not report
        # a clean pass, but the reporting below never claims the variable
        # is absent -- only that it can't be confirmed.
        self.unresolved = unresolved if unresolved is not None else set()

    @property
    def is_clean(self) -> bool:
        return not (
            self.missing or self.blank or self.invalid or self.extra or self.unresolved
        )

    def summary(self) -> str:
        # Each category names its own fix -- "here's what's wrong" without
        # "here's the command that fixes it" just relocates the diagnosis
        # work onto whoever's reading it.
        messages = []
        if self.missing:
            messages.append(
                f"Missing variables: {', '.join(sorted(self.missing))} "
                "(run 'envshield setup' to fill them in)"
            )
        if self.blank:
            messages.append(
                f"Required but blank: {', '.join(sorted(self.blank))} "
                "(run 'envshield setup' to fill them in)"
            )
        if self.invalid:
            details_str = "; ".join(
                f"{k} ({v})" for k, v in sorted(self.invalid.items())
            )
            messages.append(
                f"Invalid values: {details_str} (run 'envshield setup' to fix -- "
                "it re-validates existing values, not just missing ones)"
            )
        if self.extra:
            messages.append(
                f"Extra variables: {', '.join(sorted(self.extra))} "
                "(remove them if unused, or add them to the schema if they're meant to be there)"
            )
        if self.unresolved:
            messages.append(
                f"Cannot confirm: {', '.join(sorted(self.unresolved))} "
                "(required, but this manifest references an external ConfigMap/Secret "
                "not included in it -- it may already be supplied from there; verify "
                "manually, or include it in the manifest to let EnvShield check it)"
            )
        return "; ".join(messages)


def diff_against_schema(
    schema: Dict[str, Any],
    local_values: Dict[str, str],
    has_unresolved_source: bool = False,
) -> SchemaDiff:
    """
    Compares `local_values` (as read from a .env file, a deployment
    manifest, or anything else a parser can produce) against `schema`.

    A variable must be present and non-blank if it has a defaultValue, or
    (when it doesn't) its 'requiredIf' condition currently holds against
    the other local values -- see schema_types.should_be_present. A
    defaulted variable left absent or blank is reported the same way as
    one with no fallback at all: nothing guarantees whatever reads this
    file actually falls back the same way the schema documents, or falls
    back at all, so its own copy has to be explicit rather than implied. A
    present, non-blank value still needs to match its declared
    type/enum/pattern.

    `has_unresolved_source` (default False, so every caller that has no
    such concept -- a dotenv file, a Python module, a Compose file -- is
    completely unaffected): the parser that produced `local_values`
    reported an unresolvable external reference (currently: a Kubernetes
    envFrom.configMapRef/secretRef whose ConfigMap/Secret isn't in the
    supplied manifest, see BaseParser.has_unresolved_source) that could
    supply variable names this parser had no way to enumerate. A required
    variable not found in `local_values` is then reported as `unresolved`
    rather than `missing` -- EnvShield genuinely doesn't know whether it's
    supplied, and must not claim either way.
    """
    local_vars = set(local_values.keys())
    schema_vars_all = set(schema.keys())

    schema_vars_required = {
        key
        for key, details in schema.items()
        if schema_types.should_be_present(details, local_values)
    }

    missing = set()
    blank = set()
    unresolved = set()
    for key in schema_vars_required:
        if key not in local_values:
            if has_unresolved_source:
                unresolved.add(key)
            else:
                missing.add(key)
        elif not local_values[key]:
            blank.add(key)

    invalid: Dict[str, str] = {}
    for key, value in local_values.items():
        # A manifest parser reports a value it can't statically know (an
        # env_file reference, a bare shell pass-through, a Kubernetes
        # secretRef, an unresolvable interpolation) as BaseParser's shared
        # UNRESOLVED_VALUE placeholder rather than as missing -- validating
        # that placeholder string against a declared type/enum/pattern would
        # otherwise always fail, misreporting a legitimately-unknown value
        # as an invalid one.
        if key in schema and value and value != BaseParser.UNRESOLVED_VALUE:
            error = schema_types.validate_value(value, schema[key])
            if error:
                invalid[key] = error

    extra = local_vars - schema_vars_all

    return SchemaDiff(
        missing=missing,
        blank=blank,
        invalid=invalid,
        extra=extra,
        unresolved=unresolved,
    )


def _source_label(var: str, schema: Dict[str, Any]) -> str:
    """Describes a missing/blank variable's source for the report table -- naming its default, if it has one, so the fix is obvious without a separate lookup."""
    default = schema.get(var, {}).get("defaultValue")
    if default is not None:
        return f"env.schema.toml (default: {default!r})"
    return "env.schema.toml (Required)"


def check_schema(
    file_path: str,
    service_name: str,
    container: Optional[str] = None,
) -> bool:
    """
    Validates a local environment file against that service's
    env.schema.toml, intelligently handling variables with default values.

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

    diff = diff_against_schema(
        schema, local_values, has_unresolved_source=parser.has_unresolved_source
    )

    if diff.is_clean:
        console.print(
            "[bold green]✓ Your configuration is perfectly in sync with the schema![/bold green]"
        )
    else:
        # If there are issues, build and display a report table
        table = Table(show_header=True, header_style="bold blue")
        table.add_column("Status", style="cyan")
        table.add_column("Variable Name", style="white")
        table.add_column("Source", style="white")

        for var in sorted(diff.missing):
            table.add_row(
                "[red]Missing in Local[/red]", var, _source_label(var, schema)
            )

        for var in sorted(diff.blank):
            table.add_row("[red]Blank in Local[/red]", var, _source_label(var, schema))

        for var, reason in sorted(diff.invalid.items()):
            table.add_row("[red]Invalid Value[/red]", var, reason)

        for var in sorted(diff.extra):
            table.add_row("[yellow]Extra in Local[/yellow]", var, file_path)

        for var in sorted(diff.unresolved):
            table.add_row(
                "[yellow]Cannot Confirm[/yellow]",
                var,
                f"{file_path} (external ConfigMap/Secret reference)",
            )

        console.print(table)
        suggestions = []
        if diff.missing or diff.blank or diff.invalid:
            if parser.is_deployment_manifest:
                suggestions.append(
                    "Fix the missing/blank/invalid values directly in this deployment "
                    "manifest -- 'envshield setup' only writes your local config file, "
                    "it never edits a deployment manifest."
                )
            else:
                suggestions.append(
                    "Run 'envshield setup' to fill in missing/blank values or fix invalid ones."
                )
        if diff.extra:
            suggestions.append(
                "Remove extra variables if unused, or add them to the schema if they're meant to be there."
            )
        if diff.unresolved:
            suggestions.append(
                "Verify the 'Cannot Confirm' variables manually, or include the "
                "referenced ConfigMap/Secret in the manifest so EnvShield can check it."
            )
        console.print("\n[bold]Suggestion:[/bold] " + " ".join(suggestions))

    return diff.is_clean


def check_result(
    file_path: str,
    service_name: str,
    container: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Same validation as check_schema, but returns a plain, JSON-serializable
    dict instead of printing a Rich table -- for '--json'. Kept as its own
    function rather than a flag on check_schema, so the existing
    Rich-rendering path (and its return-value contract) is never at risk of
    a behavior change from this one.
    """
    try:
        schema = config_manager.load_schema(service_name=service_name)
        parser = get_parser(file_path, container=container, prefer=service_name)
        if not parser:
            return {
                "file": file_path,
                "service": service_name,
                "clean": False,
                "error": f"No parser found for file type '{file_path}'.",
            }
        local_values = parser.get_vars(file_path, get_values=True)
    except FileNotFoundError:
        return {
            "file": file_path,
            "service": service_name,
            "clean": False,
            "error": f"File not found: '{file_path}'.",
        }
    except (ValueError, EnvShieldException) as e:
        return {
            "file": file_path,
            "service": service_name,
            "clean": False,
            "error": str(e),
        }

    diff = diff_against_schema(
        schema, local_values, has_unresolved_source=parser.has_unresolved_source
    )
    return {
        "file": file_path,
        "service": service_name,
        "clean": diff.is_clean,
        "missing": sorted(diff.missing),
        "blank": sorted(diff.blank),
        "invalid": dict(diff.invalid),
        "extra": sorted(diff.extra),
        "unresolved": sorted(diff.unresolved),
    }


def sync_schema(service_name: str) -> bool:
    """
    Keeps a service's tracked environment template in sync with its schema
    (resolved to that service's own directory -- see
    config_manager.get_env_paths).

    Dotenv projects (the default) get '.env.example' fully regenerated from
    the schema -- it's a pure generated artifact, safe to overwrite wholesale.

    Projects whose local config is a Python module (`local_file` ends in
    '.py' -- e.g. acme's `env_config.local.py`) have no such artifact: that
    file IS the hand-maintained contract, often with logic beyond simple
    assignments. So instead of overwriting it, this only appends whatever
    schema variables are missing from it; existing lines, values, and
    surrounding code are left untouched.

    Returns whether the target actually changed -- a caller (e.g. the
    CLI's post-sync "next step" hint) needs to tell a genuine no-op apart
    from a real update, which the dotenv branch can't get for free from
    "did the write succeed": it always rewrites the file, and the header's
    own timestamp would make every write look "changed" by a naive
    before/after content comparison.
    """
    schema = config_manager.load_schema(service_name=service_name)
    paths = config_manager.get_env_paths(service_name=service_name)

    if paths["local_file"].endswith(".py"):
        return _sync_python_local_file(schema, paths["local_file"])

    output_file = paths["example_file"]
    console.print(
        f"\n[bold]Generating [cyan]{output_file}[/cyan] from schema...[/bold]"
    )

    header_marker = "# DO NOT EDIT THIS FILE MANUALLY.\n\n"
    header = (
        f"# This file was auto-generated by EnvShield on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# It is generated from the contract defined in env.schema.toml.\n"
        f"{header_marker}"
    )

    body = ""
    for key, details in schema.items():
        # A schema key is repository-controlled (env.schema.toml is
        # committed/PR-editable) and is about to become an assignment
        # target in a generated file -- it can't be escaped into a safe
        # form without changing its identity, so it's rejected outright
        # rather than sanitized. description/defaultValue remain data, so
        # they're escaped instead: a literal newline would otherwise split
        # a single '# ...' comment or 'KEY=value' line into extra physical
        # lines, injecting an unintended new line into the file.
        if not schema_types.is_safe_variable_name(key):
            raise EnvShieldException(
                f"Schema key {key!r} is not a safe variable name (must match "
                f"^[A-Za-z_][A-Za-z0-9_]*$) -- refusing to generate '{output_file}'."
            )

        description = details.get("description")
        if description:
            safe_description = description.replace("\r", "\\r").replace("\n", "\\n")
            body += f"# {safe_description}\n"

        default_value = str(details.get("defaultValue", ""))
        safe_default = default_value.replace("\r", "\\r").replace("\n", "\\n")
        body += f"{key}={safe_default}\n\n"

    old_body = None
    pre_existing_non_envshield_content = False
    if os.path.exists(output_file):
        try:
            with open(output_file, "r") as f:
                old_content = f.read()
            # header_marker only appears in a file EnvShield itself wrote on
            # a prior run -- its absence here means this call is about to
            # wholesale-replace content that predates EnvShield ever
            # touching this project (hand-authored comments, an orphaned
            # variable kept for a reason, etc.), which is otherwise silent:
            # the function is always allowed to overwrite (see this
            # function's own docstring -- '.env.example' is a generated
            # artifact by design), but the first such replacement deserves
            # to be visible, not just inferable after the fact from the
            # new file's own "DO NOT EDIT" header.
            pre_existing_non_envshield_content = header_marker not in old_content
            old_body = old_content.split(header_marker, 1)[-1]
        except OSError:
            old_body = None

    if pre_existing_non_envshield_content and old_body != body:
        console.print(
            f"[bold yellow]Note:[/bold yellow] '{output_file}' already existed "
            "and will be replaced with a version generated from the schema -- "
            "any hand-written content (comments, variables not in the schema) "
            "will not be carried over."
        )

    if old_body == body:
        console.print(f"[green]✓[/green] '{output_file}' is already up to date.")
        return False

    try:
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_file, "w") as f:
            f.write(header + body)
        console.print(
            f"[bold green]✓[/bold green] Successfully created/updated [bold cyan]{output_file}[/bold cyan]!"
        )
        return True
    except IOError as e:
        console.print(f"[red]Error:[/red] Could not write to {output_file}: {e}")
        return False


def _sync_python_local_file(schema: Dict[str, Any], local_file: str) -> bool:
    """Ensures a Python-module local config file declares every schema variable. Returns whether it actually changed."""
    if not os.path.exists(local_file):
        console.print(
            f"\n[bold]Creating [cyan]{local_file}[/cyan] from schema...[/bold]"
        )
        lines = [
            "# Auto-generated by 'envshield schema sync'.\n",
            "# Fill in real values below -- this file is your project's local config module.\n\n",
        ]
        for key, details in schema.items():
            # See sync_schema's dotenv branch above for why the key is
            # rejected rather than escaped -- here it's about to become a
            # literal Python assignment target, so an unsafe key would be
            # arbitrary injected Python source, not just a malformed name.
            if not schema_types.is_safe_variable_name(key):
                raise EnvShieldException(
                    f"Schema key {key!r} is not a safe variable name (must "
                    f"match ^[A-Za-z_][A-Za-z0-9_]*$) -- refusing to "
                    f"generate '{local_file}'."
                )

            description = details.get("description")
            if description:
                safe_description = description.replace("\r", "\\r").replace("\n", "\\n")
                lines.append(f"# {safe_description}\n")
            lines.append(f"{key} = {str(details.get('defaultValue', ''))!r}\n\n")

        output_dir = os.path.dirname(local_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with file_updater.open_new_secret_file(local_file) as f:
            f.writelines(lines)
        console.print(
            f"[bold green]✓[/bold green] Created [bold cyan]{local_file}[/bold cyan]!"
        )
        return True

    console.print(
        f"\n[bold]Checking [cyan]{local_file}[/cyan] for missing variables...[/bold]"
    )
    parser = get_parser(local_file)
    existing_vars = parser.get_vars(local_file) if parser else set()
    missing = {
        key: details for key, details in schema.items() if key not in existing_vars
    }

    if not missing:
        console.print(
            f"[bold green]✓[/bold green] [cyan]{local_file}[/cyan] already declares every schema variable."
        )
        return False

    updates = [
        {"key": key, "value": str(details.get("defaultValue", ""))}
        for key, details in missing.items()
    ]
    file_updater.update_variables_in_file(local_file, updates)
    console.print(
        f"[bold green]✓[/bold green] Added {len(updates)} missing variable(s) to [bold cyan]{local_file}[/bold cyan]: {', '.join(missing.keys())}"
    )
    return True
