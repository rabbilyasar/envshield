# envshield/core/setup_manager.py
# Contains the core business logic for the 'setup' command.

import dataclasses
import datetime
import os
import sys
from typing import Any, Dict, List, Optional

import questionary
from rich.console import Console
from rich.prompt import Prompt

from ..config import manager as config_manager
from ..parsers.factory import get_parser
from . import file_updater, schema_manager, schema_types, service_discovery
from .exceptions import EnvShieldException, SchemaNotFoundError
from .importer import key_contains_secret_keyword

console = Console()
EXAMPLE_FILE = ".env.example"


# Outcome categories (V3-2, #21). NEEDS ACCESS is produced once provider
# references exist (V3-3, #22).
READY = "READY"
NEEDS_ACCESS = "NEEDS ACCESS"
SET_YOURSELF = "SET YOURSELF"
OPTIONAL = "OPTIONAL"


@dataclasses.dataclass
class Outcome:
    """What setup did, or couldn't do, for one variable. Never a value."""

    name: str
    category: str
    detail: str
    required: bool


@dataclasses.dataclass
class SetupResult:
    """
    What happened for one service's setup run: every variable's outcome,
    and what was written. `completed` is always True since #20 (there's no
    whole-file prompt to decline); `ready` is whether every variable
    required locally is READY, which decides the exit code (#21).
    """

    completed: bool
    local_file: str
    configured_count: int = 0
    outcomes: List[Outcome] = dataclasses.field(default_factory=list)
    added: int = 0
    changed: int = 0

    def __bool__(self) -> bool:
        return self.completed

    @property
    def required_total(self) -> int:
        return sum(1 for o in self.outcomes if o.required)

    @property
    def required_ready(self) -> int:
        return sum(1 for o in self.outcomes if o.required and o.category == READY)

    @property
    def ready(self) -> bool:
        return self.required_ready == self.required_total

    @property
    def to_set_yourself(self) -> List[str]:
        return [o.name for o in self.outcomes if o.category == SET_YOURSELF]


def stdin_is_terminal() -> bool:
    """Whether setup can prompt (#21). Tests replace this to simulate one."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _fill_schema_defaults(
    schema: Dict[str, Any], values: Dict[str, str]
) -> Dict[str, str]:
    """
    Returns a copy of `values` with every schema default filled in for any
    key that doesn't already have a truthy value -- the same resolution
    'setup' always performs before deciding what actually still needs a
    prompt. Computed once and reused both for the upfront drift
    classification and for Step 1's real prompt-target dict below, so the
    two can never diverge on what counts as "already resolved" -- a
    defaulted field is never something the user needs to act on, so
    classifying it against pre-default values (as `check`/`doctor` do,
    correctly, since they never auto-fill anything) would otherwise call
    it "missing" or "blank" right before silently filling it with zero
    prompt, contradicting the very next thing 'setup' does.
    """
    resolved = dict(values)
    for key, field_schema in schema.items():
        # Only an absent key (an explicit empty value is the developer's),
        # and never a secret (#20).
        if (
            key not in resolved
            and "defaultValue" in field_schema
            and not field_schema.get("secret")
        ):
            resolved[key] = field_schema["defaultValue"]
    return resolved


def _print_drift_summary(
    schema: Dict[str, Any], diff: schema_manager.SchemaDiff
) -> None:
    """
    Shows the gap between the schema and what's already on disk, before
    anything is prompted for -- reusing check/doctor's own classification
    (`schema_manager.diff_against_schema`) rather than a second engine.
    'missing' is split by whether the variable is unconditionally required
    or only required right now because of an active 'requiredIf' -- the
    schema itself proves that distinction, so it's never a claim about
    history (e.g. never "newly required", which nothing here could
    actually prove). Silent when there's nothing to report; the plain
    "no empty or invalid variables" message below already covers that.
    """
    if diff.is_clean:
        return
    if diff.missing:
        unconditional = sorted(
            k for k in diff.missing if not schema.get(k, {}).get("requiredIf")
        )
        conditional = sorted(k for k in diff.missing if k not in unconditional)
        if unconditional:
            console.print(f"  [yellow]missing:[/yellow] {', '.join(unconditional)}")
        if conditional:
            console.print(
                f"  [yellow]missing (conditionally required):[/yellow] {', '.join(conditional)}"
            )
    if diff.blank:
        console.print(f"  [yellow]blank:[/yellow] {', '.join(sorted(diff.blank))}")
    if diff.invalid:
        for key, reason in sorted(diff.invalid.items()):
            console.print(f"  [red]invalid:[/red] {key} ({reason})")


def _is_secret_key(key: str) -> bool:
    """
    Fallback heuristic for whether a key is likely a secret, used only when
    the key isn't declared in env.schema.toml. Whenever the schema declares
    the variable, its explicit `secret` flag is authoritative -- see
    run_setup() -- so this never overrides a documented decision.
    """
    return key_contains_secret_keyword(key)


def _read_values(path: str) -> Dict[str, str]:
    """The variables `path` assigns, with their values ("" for `KEY=`)."""
    if not os.path.exists(path):
        return {}
    parser = get_parser(path)
    if not parser:
        return {}
    try:
        values = parser.get_vars(path, get_values=True)
    except FileNotFoundError:
        return {}
    return {k: ("" if v is None else str(v)) for k, v in values.items()}


def _secret(key: str, field_schema: Dict[str, Any], declared: bool = True) -> bool:
    """A declared variable is secret only if the schema says so; the name
    heuristic is for a variable the schema doesn't declare."""
    if declared:
        return bool(field_schema.get("secret", False))
    return _is_secret_key(key)


class _Skip(Exception):
    """The developer left a prompt blank: the variable stays as it is."""


def _explain(key: str, field_schema: Dict[str, Any], schema, resolved) -> None:
    description = field_schema.get("description")
    if description and not description.startswith("TODO"):
        console.print(f"  [dim]{description}[/dim]")
    if field_schema.get("requiredIf") and schema_types.is_required_now(
        field_schema, resolved
    ):
        condition_text = schema_types.requiredif_condition_text(field_schema, schema)
        if condition_text:
            console.print(f"  [dim]Required because {condition_text}.[/dim]")


def _prompt_value(
    key: str,
    field_schema: Dict[str, Any],
    schema: Dict[str, Any],
    resolved: Dict[str, str],
) -> str:
    """
    Asks for one value (masked for a secret; a non-secret's default is the
    suggestion). A blank answer, or three invalid ones, raises _Skip: the
    variable is left for the developer to set (V3-2 reports it).
    """
    _explain(key, field_schema, schema, resolved)
    secret = _secret(key, field_schema, declared=key in schema)
    suggestion = None
    if not secret and "defaultValue" in field_schema:
        suggestion = str(field_schema["defaultValue"])
    if suggestion is not None:
        # With a default, the developer chooses explicitly, so declining is
        # possible: pressing Enter in a text prompt would take the default.
        use_default = f"Use the default ({suggestion})"
        choice = questionary.select(
            f"  {key}:",
            choices=[use_default, "Enter a value", "Leave it as it is"],
        ).ask()
        if choice is None:
            raise KeyboardInterrupt
        if choice == use_default:
            return suggestion
        if choice.startswith("Leave"):
            raise _Skip
        suggestion = None
    enum_choices = schema_types.enum_values(field_schema)
    if enum_choices:
        skip = "(leave it unset for now)"
        chosen = questionary.select(
            f"  Please select a value for {key}",
            choices=enum_choices + [skip],
            default=None,
        ).ask()
        if chosen is None:
            raise KeyboardInterrupt
        if chosen == skip:
            raise _Skip
        return chosen
    for attempt in range(1, 4):
        value = Prompt.ask(
            f"  Please enter the value for [bold cyan]{key}[/bold cyan] (Enter to skip)",
            password=secret,
        )
        if not value:
            raise _Skip
        error = schema_types.validate_value(value, field_schema)
        if not error:
            return value
        console.print(f"  [red]✗ {error}[/red]")
    console.print(
        f"  [yellow]{key} left unset after 3 invalid values; set it yourself.[/yellow]"
    )
    raise _Skip


def run_setup(
    service_name: str, output_file: Optional[str] = None, interactive: bool = True
) -> SetupResult:
    """
    Guides a developer through creating or completing their local
    configuration (V3-1, #20): every variable is resolved first, then the
    file is written once, atomically, in place, and every variable's
    outcome is reported (V3-2, #21).

    Per variable, in order: (1) a value already in the local file, an
    explicit empty one included, is kept, and replaced only after
    confirmation; (3) the schema's `defaultValue`, only for an absent,
    non-secret key; (4) the `.env.example` value, only when non-secret,
    non-empty and valid; (5) a prompt, only for a variable required now
    (masked for a secret; the developer may skip); (6) otherwise it stays
    unset. (Step 2, provider references, is V3-3.) Optional variables are
    never prompted. No value is ever printed.

    `interactive=False` (--non-interactive, or no terminal) never prompts:
    whatever can be resolved without asking is written, the rest is
    reported, and every existing line stays as it is.
    """
    paths = config_manager.get_env_paths(service_name=service_name)
    example_file = paths["example_file"]
    local_file = output_file or paths["local_file"]
    is_python_target = local_file.endswith(".py")

    console.print(f"[bold]{local_file}[/bold]")
    # Refuse an unsafe target before asking for any value (BL-154).
    file_updater.assert_safe_write_target(local_file)

    # A schema that doesn't exist yet falls back to the template alone; one
    # that fails to load is an error before anything is resolved (BL-153).
    missing_schema: Optional[str] = None
    try:
        schema = config_manager.load_schema(service_name=service_name)
    except SchemaNotFoundError as e:
        schema = {}
        missing_schema = str(e)
    # A local file several services share is written from the union of
    # their projections (the physical-file contract).
    if schema and config_manager.same_physical_file(local_file, paths["local_file"]):
        schema = config_manager.load_file_contract(service_name, "local_file")

    local_exists = os.path.exists(local_file)
    local = _read_values(local_file) if local_exists else {}
    example = (
        _read_values(example_file)
        if not config_manager.same_physical_file(example_file, local_file)
        else {}
    )
    if not schema and not local and not example:
        if is_python_target:
            raise EnvShieldException(
                f"'{local_file}' not found, and no schema exists to generate one from. Run 'envshield init --from <file>' or 'envshield schema sync' first."
            )
        raise EnvShieldException(
            f"'{example_file}' not found. Please run 'envshield schema sync' first to generate it."
        )
    if missing_schema:
        console.print(
            f"[bold yellow]Warning:[/bold yellow] {missing_schema} Values are taken "
            "from the existing files as they are, without validation."
        )
    if schema and interactive:
        # An overview before prompting; empties are reported.
        _print_drift_summary(
            schema,
            schema_manager.diff_against_schema(
                schema, _fill_schema_defaults(schema, local)
            ),
        )

    keys = list(schema.keys()) or [k for k in example if k not in local]
    additions: Dict[str, str] = {}
    replacements: Dict[str, str] = {}
    removals: List[str] = []
    outcomes: Dict[str, Outcome] = {}
    sources: Dict[str, str] = {}
    resolved = dict(local)

    def hint(key: str, reason: str) -> str:
        description = schema.get(key, {}).get("description") or ""
        if description and description != schema_types.PLACEHOLDER_DESCRIPTION:
            return f"{reason}; {description}"
        return reason

    # Steps 3-4 for every absent key first, so a requiredIf can see a
    # sibling's default or template value.
    for key in keys:
        field_schema = schema.get(key, {})
        if key in local:
            continue
        if not schema:
            # No schema: the template's values are taken as they are (the
            # warning above says so); empty ones are asked for.
            if example.get(key):
                additions[key] = example[key]
                sources[key] = f"from {os.path.basename(example_file)}"
                resolved[key] = additions[key]
            continue
        if _secret(key, field_schema):
            continue
        if "defaultValue" in field_schema:
            additions[key] = str(field_schema["defaultValue"])
            sources[key] = "default"
        elif example.get(key) and not schema_types.validate_value(
            example[key], field_schema
        ):
            additions[key] = example[key]
            sources[key] = f"from {os.path.basename(example_file)}"
        else:
            continue
        resolved[key] = additions[key]

    asked_header = False

    def outcome(key: str, category: str, detail: str, required: bool) -> None:
        outcomes[key] = Outcome(key, category, detail, required)

    for key in keys:
        field_schema = schema.get(key, {})
        required = False
        try:
            required = schema_types.should_be_present(field_schema, resolved)
            if key in local:
                value = local[key]
                error = (
                    schema_types.validate_value(value, field_schema)
                    if key in schema
                    else None
                )
                if value == "" and required:
                    console.print(f"\n[yellow]{key} is set but empty.[/yellow]")
                    reason = "set but empty"
                elif error and value == "":
                    console.print(
                        f"\n[yellow]{key} is set but empty, and empty isn't valid "
                        f"({error}).[/yellow]"
                    )
                    reason = f"set but empty, and empty isn't valid ({error})"
                    if not interactive:
                        outcome(key, OPTIONAL, reason, required)
                        continue
                    choice = questionary.select(
                        f"  What should happen to {key}?",
                        choices=[
                            "Enter a value",
                            "Remove the line (leave it unset)",
                            "Keep it empty",
                        ],
                    ).ask()
                    if choice is None:
                        raise KeyboardInterrupt
                    if choice.startswith("Remove"):
                        removals.append(key)
                        resolved.pop(key, None)
                        outcome(key, OPTIONAL, "line removed", required)
                        continue
                    if choice.startswith("Keep"):
                        outcome(key, OPTIONAL, reason, required)
                        continue
                elif error:
                    console.print(
                        f"\n[yellow]{key} has an invalid value ({error}).[/yellow]"
                    )
                    reason = f"invalid ({error})"
                    if (
                        not interactive
                        or not questionary.confirm(
                            f"  Replace {key}'s value?", default=True
                        ).ask()
                    ):
                        outcome(
                            key,
                            SET_YOURSELF if required else OPTIONAL,
                            hint(key, reason) if required else reason,
                            required,
                        )
                        continue
                else:
                    outcome(
                        key,
                        READY if (value != "" or required) else OPTIONAL,
                        "already set" if value != "" else "set but empty",
                        required,
                    )
                    continue
                if not interactive:
                    outcome(key, SET_YOURSELF, hint(key, reason), required)
                    continue
                try:
                    replacements[key] = _prompt_value(
                        key, field_schema, schema, resolved
                    )
                    resolved[key] = replacements[key]
                    outcome(key, READY, "entered", required)
                except _Skip:
                    outcome(
                        key,
                        SET_YOURSELF if required else OPTIONAL,
                        hint(key, reason),
                        required,
                    )
                continue
            if key in additions:
                outcome(key, READY, sources.get(key, "default"), required)
                continue
            if not required:
                outcome(key, OPTIONAL, "not required locally", required)
                continue
            if not interactive:
                outcome(key, SET_YOURSELF, hint(key, "no shared source"), required)
                continue
            if not asked_header:
                console.print(
                    "\n[bold]Please provide values for the following variables:[/bold]"
                )
                asked_header = True
            try:
                additions[key] = _prompt_value(key, field_schema, schema, resolved)
                resolved[key] = additions[key]
                outcome(key, READY, "entered", required)
            except _Skip:
                outcome(key, SET_YOURSELF, hint(key, "skipped"), required)
        except EnvShieldException as e:
            # One variable's problem never stops the others.
            console.print(f"[red]✗ {key}: {e}[/red]")
            outcome(key, SET_YOURSELF, str(e), True)

    # One atomic write, after everything is resolved: an interruption
    # before this point leaves the file as it was.
    changed = bool(additions or replacements or removals)
    if changed or not local_exists:
        _write_changes(
            local_file, local_exists, keys, additions, replacements, removals
        )
    result = SetupResult(
        completed=True,
        local_file=local_file,
        configured_count=len(additions) + len(replacements) + len(removals),
        outcomes=[outcomes[k] for k in keys if k in outcomes],
        added=len(additions),
        changed=len(replacements) + len(removals),
    )
    _print_report(result, local_exists)
    return result


def _print_report(result: SetupResult, existed: bool) -> None:
    """
    Every variable's outcome, then the summary line (#21). Names, categories
    and the contract's descriptions only: never a value.
    """
    if result.outcomes:
        width = max(len(o.name) for o in result.outcomes)
        console.print()
        styles = {
            READY: "green",
            NEEDS_ACCESS: "yellow",
            SET_YOURSELF: "yellow",
            OPTIONAL: "dim",
        }
        for o in result.outcomes:
            style = styles[o.category]
            console.print(
                f"  [{style}]{o.category:<12}[/{style}]  {o.name:<{width}}  "
                f"[dim]{o.detail}[/dim]",
                highlight=False,
            )
    if not existed:
        written = f"{result.local_file} created ({result.added} added)"
    elif result.added or result.changed:
        written = (
            f"{result.local_file} updated ({result.added} added, "
            f"{result.changed} changed)"
        )
    else:
        written = f"{result.local_file} unchanged"
    console.print(
        f"\n{result.required_ready} of {result.required_total} required variables "
        f"ready. {written}."
    )


def _write_changes(
    local_file: str,
    local_exists: bool,
    keys: List[str],
    additions: Dict[str, str],
    replacements: Dict[str, str],
    removals: List[str],
) -> None:
    """The single write: in place when the file exists, else a new file."""
    ordered = [k for k in keys if k in additions] + [
        k for k in additions if k not in keys
    ]
    if not local_exists:
        if local_file.endswith(".py"):
            _write_python_local_file(local_file, {k: additions[k] for k in ordered}, [])
        else:
            _write_dotenv_local_file(local_file, {k: additions[k] for k in ordered})
        return
    try:
        with open(local_file, "r") as f:
            lines = f.readlines()
    except OSError as e:
        raise EnvShieldException(f"Could not read '{local_file}': {e}")
    updates = [{"key": k, "value": v} for k, v in replacements.items()] + [
        {"key": k, "value": additions[k]} for k in ordered
    ]
    new_lines = file_updater.patch_variable_lines(
        lines, updates, local_file, removals=removals
    )
    try:
        file_updater.atomic_write(local_file, "".join(new_lines), secret=True)
    except OSError as e:
        raise EnvShieldException(f"Could not write to '{local_file}': {e}")


def _write_dotenv_local_file(local_file: str, final_vars: Dict[str, str]) -> None:
    """A new dotenv local file with exactly `final_vars`, written atomically."""
    # Every key is validated before anything is written: it becomes the
    # left-hand side of a 'KEY=value' line and can't be escaped safely.
    lines = [
        f"{service_discovery.ENVSHIELD_GENERATED_MARKER} on {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
    ]
    for key, value in final_vars.items():
        lines.append(file_updater.render_assignment(key, value, False, local_file))
    try:
        # 0600 whether or not the file existed; refuses a symlinked or
        # out-of-project target (BL-154).
        file_updater.atomic_write(local_file, "".join(lines), secret=True)
    except OSError as e:
        raise EnvShieldException(f"Could not write to '{local_file}': {e}")
    console.print(
        f"\n[bold green]✓ Successfully created your [magenta]{local_file}[/magenta] file![/bold green]"
    )


def _write_python_local_file(
    local_file: str, final_vars: Dict[str, str], prompted_keys: List[str]
) -> None:
    """
    A new Python-module local file with `final_vars`, or -- if one exists --
    only `prompted_keys` and the variables it doesn't assign yet patched in
    place. Written atomically either way.
    """
    if not os.path.exists(local_file):
        lines = [
            f"{service_discovery.ENVSHIELD_GENERATED_MARKER} on {datetime.datetime.now().strftime('%Y-%m-%d')}\n\n"
        ]
        for key, value in final_vars.items():
            lines.append(file_updater.render_assignment(key, value, True, local_file))
        try:
            file_updater.atomic_write(local_file, "".join(lines), secret=True)
        except OSError as e:
            raise EnvShieldException(f"Could not write to '{local_file}': {e}")
        console.print(
            f"\n[bold green]✓ Successfully created your [magenta]{local_file}[/magenta] file![/bold green]"
        )
        return
    parser = get_parser(local_file)
    existing_keys = parser.get_vars(local_file) if parser else set()
    keys_needing_write = set(prompted_keys) | (set(final_vars) - existing_keys)
    if not keys_needing_write:
        console.print(
            f"[green]✓ '{local_file}' already has values for every variable.[/green]"
        )
        return
    updates = [{"key": key, "value": final_vars[key]} for key in keys_needing_write]
    # A local secrets file: left 0600.
    file_updater.update_variables_in_file(local_file, updates, secret=True)
    console.print(
        f"\n[bold green]✓ Updated [magenta]{local_file}[/magenta] with {len(updates)} value(s): {', '.join(sorted(keys_needing_write))}[/bold green]"
    )
