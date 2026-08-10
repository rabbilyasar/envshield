# envshield/core/setup_manager.py
# Contains the core business logic for the 'setup' command.

import datetime
import os
import re
from typing import Dict, List, Optional

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from ..config import manager as config_manager
from ..parsers.factory import get_parser
from . import file_updater, schema_types, service_discovery
from .exceptions import EnvShieldException
from .importer import key_contains_secret_keyword

console = Console()
EXAMPLE_FILE = ".env.example"


def _is_secret_key(key: str) -> bool:
    """
    Fallback heuristic for whether a key is likely a secret, used only when
    the key isn't declared in env.schema.toml. Whenever the schema declares
    the variable, its explicit `secret` flag is authoritative -- see
    run_setup() -- so this never overrides a documented decision.
    """
    return key_contains_secret_keyword(key)


def _read_seed_values(example_file: str, local_file: str) -> Dict[str, str]:
    """
    Determines each variable's starting value: whatever the project's local
    file already has (if it exists -- e.g. a new dev re-running setup, or a
    Python module like acme's `env_config.local.py` that's checked into git
    with working values already), falling back to the tracked template
    (`.env.example`) when the local file doesn't exist yet at all.
    """
    for candidate in (local_file, example_file):
        if not os.path.exists(candidate):
            continue
        parser = get_parser(candidate)
        if not parser:
            continue
        try:
            return parser.get_vars(candidate, get_values=True)
        except FileNotFoundError:
            continue
    return {}


def run_setup(service_name: str, output_file: Optional[str] = None):
    """
    Guides a new developer through creating (or completing) their local
    environment config, driven by the service's schema.

    Args:
        output_file: Explicit path to write to. If omitted, resolves to the
            service's local file -- '.env' by default, or whatever
            'local_file' is set to for this service in envshield.yml (see
            config_manager.get_env_paths).
        service_name: Which registered service to set up.
    """
    paths = config_manager.get_env_paths(service_name=service_name)
    example_file = paths["example_file"]
    local_file = output_file or paths["local_file"]
    is_python_target = local_file.endswith(".py")

    console.print(
        Panel(
            f"[bold cyan]Welcome to EnvShield Setup[/bold cyan]\n\nThis wizard will help you set up your local [magenta]{local_file}[/magenta] file.",
            title="✨ Local Setup ✨",
            border_style="green",
        )
    )

    # Load the schema so we can use its authoritative 'secret' flag and
    # descriptions during prompting, instead of re-guessing from the key name.
    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException:
        schema = {}

    seed_values = _read_seed_values(example_file, local_file)

    if not schema and not seed_values:
        if is_python_target:
            raise EnvShieldException(
                f"'{local_file}' not found, and no schema exists to generate one from. Run 'envshield import' or 'envshield schema sync' first."
            )
        raise EnvShieldException(
            f"'{example_file}' not found. Please run 'envshield schema sync' first to generate it."
        )

    # A full rewrite only makes sense for a pure generated artifact (dotenv).
    # A Python module may hold real logic beyond simple assignments, so it's
    # only ever patched in place -- see the write step below -- and never
    # needs an "overwrite?" confirmation.
    if not is_python_target and os.path.exists(local_file):
        overwrite = questionary.confirm(
            f"A '{local_file}' file already exists. Do you want to overwrite it?",
            default=False,
        ).ask()
        if not overwrite:
            console.print("[yellow]Setup cancelled.[/yellow]")
            return

    # Step 1: Work out which variables already have a usable value (from the
    # local file, the template, or the schema's own default), and which still
    # need to be asked for. Schema vars come first (they're the contract);
    # any extra vars already present locally are carried over untouched.
    final_vars: Dict[str, str] = dict(seed_values)
    keys_to_prompt: List[str] = []
    all_keys = list(schema.keys()) + [k for k in seed_values if k not in schema]

    # Fill in every schema default first, regardless of key order, so a
    # 'requiredIf' condition can be evaluated against a sibling's default
    # value below even when that sibling comes later in the schema.
    for key, field_schema in schema.items():
        if not final_vars.get(key) and "defaultValue" in field_schema:
            final_vars[key] = field_schema["defaultValue"]

    for key in all_keys:
        field_schema = schema.get(key, {})
        existing = final_vars.get(key)
        if existing:
            # Already has a value -- only re-prompt if it's actually invalid
            # against the schema (e.g. hand-edited to something the
            # enum/pattern/type no longer allows). A var with no schema
            # entry at all (an extra, already-present local var) is never
            # second-guessed.
            if key not in schema or not schema_types.validate_value(
                existing, field_schema
            ):
                continue
        else:
            if "defaultValue" in field_schema:
                continue  # already filled above
            if not schema_types.is_required_now(field_schema, final_vars):
                continue  # not required right now (unmet 'requiredIf') -- don't nag for it
        keys_to_prompt.append(key)

    # Step 2: Prompt for whatever's still missing (or invalid)
    if not keys_to_prompt:
        console.print(
            "[green]✓ No empty or invalid variables found to configure.[/green]"
        )
    else:
        console.print(
            "\n[bold]Please provide values for the following variables:[/bold]"
        )
        for key in keys_to_prompt:
            field_schema = schema.get(key, {})
            description = field_schema.get("description")
            if description and not description.startswith("TODO"):
                console.print(f"  [dim]{description}[/dim]")

            is_secret = (
                field_schema["secret"]
                if "secret" in field_schema
                else _is_secret_key(key)
            )
            enum_choices = schema_types.enum_values(field_schema)

            if enum_choices:
                # A picker can't produce an invalid value -- no retry loop needed.
                existing = final_vars.get(key)
                new_value = questionary.select(
                    f"  Please select a value for {key}",
                    choices=enum_choices,
                    default=existing if existing in enum_choices else None,
                ).ask()
                if new_value is None:
                    raise EnvShieldException("Setup cancelled by user.")
            else:
                # A field reaching this prompt is either currently required
                # (should_be_present) or has an existing value invalid
                # enough to need fixing -- blank is only a legitimate
                # answer for the latter when it's genuinely optional
                # (no default, requiredIf unmet). Accepting blank
                # unconditionally here would let 'setup' itself produce
                # exactly the "Blank in Local" failure 'check'/'doctor'
                # exist to catch, defeating the wizard's whole purpose.
                must_be_present = schema_types.should_be_present(
                    field_schema, final_vars
                )
                new_value = ""
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    new_value = Prompt.ask(
                        f"  Please enter the value for [bold cyan]{key}[/bold cyan]",
                        password=is_secret,
                    )
                    if not new_value:
                        error = (
                            "This value is required and cannot be left blank."
                            if must_be_present
                            else None
                        )
                    else:
                        error = schema_types.validate_value(new_value, field_schema)
                    if not error:
                        break
                    console.print(f"  [red]✗ {error}[/red]")
                    if attempt == max_attempts:
                        console.print(
                            f"  [yellow]Keeping this value after {max_attempts} attempts -- fix it later with 'envshield check'.[/yellow]"
                        )
            final_vars[key] = new_value

    # Step 3: Write the result
    if is_python_target:
        _write_python_local_file(local_file, final_vars, keys_to_prompt)
    else:
        _write_dotenv_local_file(local_file, final_vars)


def _write_dotenv_local_file(local_file: str, final_vars: Dict[str, str]) -> None:
    """Fully regenerates a dotenv-style local file -- safe, since it's a plain generated artifact."""
    # Validated up front, before anything is written: a schema key is
    # about to become the left-hand side of a 'KEY=value' line, and can't
    # be escaped into a safe form without changing its identity, so it's
    # rejected outright rather than sanitized. Checking every key before
    # opening the file (rather than mid-loop) avoids leaving behind a
    # truncated, half-written file if a later key turns out to be unsafe.
    for key in final_vars:
        if not schema_types.is_safe_variable_name(key):
            raise EnvShieldException(
                f"Schema key {key!r} is not a safe variable name (must "
                f"match ^[A-Za-z_][A-Za-z0-9_]*$) -- refusing to write "
                f"'{local_file}'."
            )

    try:
        output_dir = os.path.dirname(local_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(local_file, "w") as f:
            f.write(
                f"{service_discovery.ENVSHIELD_GENERATED_MARKER} on {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
            )
            for key, value in final_vars.items():
                # A literal newline/carriage-return would otherwise split into
                # extra physical lines -- potentially injecting an unintended
                # new KEY=VALUE assignment into the file.
                safe_value = value.replace("\n", "\\n").replace("\r", "\\r")
                if (
                    re.search(r"[#\s=]", safe_value)
                    and not (safe_value.startswith("'") and safe_value.endswith("'"))
                    and not (safe_value.startswith('"') and safe_value.endswith('"'))
                ):
                    f.write(f'{key}="{safe_value}"\n')
                else:
                    f.write(f"{key}={safe_value}\n")
        console.print(
            f"\n[bold green]✓ Successfully created your [magenta]{local_file}[/magenta] file![/bold green]"
        )
    except IOError as e:
        raise EnvShieldException(f"Could not write to '{local_file}': {e}")


def _write_python_local_file(
    local_file: str, final_vars: Dict[str, str], prompted_keys: List[str]
) -> None:
    """
    Creates a fresh Python-module local file, or -- if one already exists --
    patches only what changed: newly-prompted values, plus any schema
    variable that isn't declared in the file at all yet (even if it was
    silently filled from a schema default rather than prompted). Everything
    else already in the file is left completely untouched.
    """
    if not os.path.exists(local_file):
        # Same reasoning as _write_dotenv_local_file above -- validated
        # before anything is written, since here an unsafe key would be
        # arbitrary injected Python source once this module is imported,
        # not just a malformed name.
        for key in final_vars:
            if not schema_types.is_safe_variable_name(key):
                raise EnvShieldException(
                    f"Schema key {key!r} is not a safe variable name (must "
                    f"match ^[A-Za-z_][A-Za-z0-9_]*$) -- refusing to write "
                    f"'{local_file}'."
                )

        try:
            output_dir = os.path.dirname(local_file)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(local_file, "w") as f:
                f.write(
                    f"{service_discovery.ENVSHIELD_GENERATED_MARKER} on {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
                )
                for key, value in final_vars.items():
                    f.write(f"{key} = {value!r}\n")
            console.print(
                f"\n[bold green]✓ Successfully created your [magenta]{local_file}[/magenta] file![/bold green]"
            )
        except IOError as e:
            raise EnvShieldException(f"Could not write to '{local_file}': {e}")
        return

    parser = get_parser(local_file)
    existing_keys = parser.get_vars(local_file) if parser else set()
    keys_needing_write = set(prompted_keys) | (set(final_vars.keys()) - existing_keys)

    if not keys_needing_write:
        console.print(
            f"[green]✓ '{local_file}' already has values for every variable.[/green]"
        )
        return

    updates = [{"key": key, "value": final_vars[key]} for key in keys_needing_write]
    file_updater.update_variables_in_file(local_file, updates)
    console.print(
        f"\n[bold green]✓ Updated [magenta]{local_file}[/magenta] with {len(updates)} value(s): {', '.join(sorted(keys_needing_write))}[/bold green]"
    )
