# envshield/core/schema_manager.py
import datetime
import os
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from rich.console import Console
from rich.table import Table

from envshield.config import manager as config_manager
from envshield.core import file_updater, schema_types
from envshield.core.exceptions import EnvShieldException
from envshield.parsers._base import BaseParser
from envshield.parsers._deployment import looks_like_unrendered_helm_template
from envshield.parsers.factory import get_parser

console = Console()


def _no_parser_found_message(file_path: str) -> str:
    """
    The message to show when get_parser() returns None for `file_path`.

    Detects the specific, common cause of a Helm chart template checked
    directly instead of its rendered output -- a '.yml'/'.yaml' file
    get_parser() couldn't recognize as either a docker-compose or
    Kubernetes manifest because it isn't valid YAML at all, on account of
    unrendered '{{ ... }}' Go-template syntax -- and gives a specific,
    actionable message for that case instead of the generic one.
    """
    _, extension = os.path.splitext(file_path)
    if extension in (".yml", ".yaml") and looks_like_unrendered_helm_template(
        file_path
    ):
        return (
            f"'{file_path}' looks like an unrendered Helm chart template (it "
            "contains '{{ ... }}' template syntax). Render it first with "
            "'helm template' -- EnvShield validates a Kubernetes manifest's "
            "final YAML, not Helm's template source."
        )
    return f"No parser found for file type '{file_path}'."


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


class UnionSource(NamedTuple):
    """
    One registered source's already-loaded local values, ready for
    `completeness: union` evaluation (BL-030). `label` is the source's own
    path, used only for reporting -- never persisted, never used to decide
    a value conflict (see `evaluate_union_completeness`'s explicit
    ambiguity rule for why "which source" is deliberately not a
    tie-breaker).
    """

    label: str
    local_values: Dict[str, str]
    has_unresolved_source: bool = False


class UnionCompletenessResult:
    """
    The pure result of evaluating a schema against the UNION of several
    registered sources' presence -- `completeness: union`'s counterpart to
    `SchemaDiff`, deliberately kept close to its vocabulary (missing/blank/
    invalid/unresolved) since it answers the same underlying question
    ("is this schema satisfied") for many sources at once, plus one new
    category (`ambiguous_requiredif`) that has no single-source equivalent:
    a `requiredIf` trigger asserted with conflicting values by more than
    one registered source, which makes that field's own required-ness
    undecidable rather than merely unmet.
    """

    def __init__(
        self,
        missing: set,
        blank: set,
        invalid: Dict[str, str],
        unresolved: set,
        ambiguous_requiredif: Dict[str, str],
    ):
        self.missing = missing
        self.blank = blank
        self.invalid = invalid
        self.unresolved = unresolved
        self.ambiguous_requiredif = ambiguous_requiredif

    @property
    def is_clean(self) -> bool:
        return not (
            self.missing
            or self.blank
            or self.invalid
            or self.unresolved
            or self.ambiguous_requiredif
        )

    def summary(self) -> str:
        messages = []
        if self.missing:
            messages.append(
                f"Missing from every registered source: {', '.join(sorted(self.missing))}"
            )
        if self.blank:
            messages.append(
                f"Blank everywhere it's declared: {', '.join(sorted(self.blank))}"
            )
        if self.invalid:
            details_str = "; ".join(
                f"{k} ({v})" for k, v in sorted(self.invalid.items())
            )
            messages.append(f"Invalid values: {details_str}")
        if self.unresolved:
            messages.append(
                f"Cannot confirm (a registered source has an unresolved external "
                f"reference): {', '.join(sorted(self.unresolved))}"
            )
        if self.ambiguous_requiredif:
            for key, reason in sorted(self.ambiguous_requiredif.items()):
                messages.append(f"{key}: {reason}")
        return "; ".join(messages)


def _union_key_status(
    key: str, field_schema: Dict[str, Any], sources: List[UnionSource]
) -> str:
    """
    Classifies one schema key's presence across all `sources`, mirroring
    `diff_against_schema`'s own single-source presence/validity logic
    (including its `BaseParser.UNRESOLVED_VALUE` carve-out) generalized to
    N sources. Deliberately never retains *which* value or source
    satisfied the key -- completeness: union unions presence, not values
    (see this module's own docstring-level framing of that boundary).

    Returns "ok" (satisfied somewhere), "blank" (present, but only ever
    blank), an `"invalid:<reason>"` string (present and non-blank
    somewhere, but never valid), or "absent" (not found in any source).
    """
    saw_blank = False
    invalid_reason: Optional[str] = None
    for source in sources:
        if key not in source.local_values:
            continue
        value = source.local_values[key]
        if value == BaseParser.UNRESOLVED_VALUE:
            return "ok"
        if not value:
            saw_blank = True
            continue
        error = schema_types.validate_value(value, field_schema)
        if error:
            if invalid_reason is None:
                invalid_reason = error
            continue
        return "ok"
    if invalid_reason is not None:
        return f"invalid:{invalid_reason}"
    if saw_blank:
        return "blank"
    return "absent"


def _resolve_union_requiredif_triggers(
    schema: Dict[str, Any], sources: List[UnionSource]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Resolves every `requiredIf` trigger variable referenced anywhere in
    `schema` against the union of `sources`.

    A trigger absent from every source resolves to nothing (the dependent
    field's condition then evaluates as not-met, via schema_types.
    is_required_now's own existing `None == expected` behavior -- exactly
    today's single-source semantics, just extended to the union). A
    trigger asserted with the same value by every source that has it
    resolves normally. A trigger asserted with genuinely *different*
    values by different sources is never resolved by picking one --
    registration order must never decide completeness (explicit product
    decision) -- it's recorded as ambiguous instead, with every
    conflicting value and its source(s) named.

    Returns (resolved trigger values -- safe to feed straight into
    schema_types.is_required_now/should_be_present unchanged, ambiguous
    trigger name -> human-readable diagnostic).
    """
    trigger_vars = {
        details["requiredIf"]["var"]
        for details in schema.values()
        if isinstance(details.get("requiredIf"), dict) and details["requiredIf"].get("var")
    }

    resolved: Dict[str, str] = {}
    ambiguous: Dict[str, str] = {}
    for trigger in trigger_vars:
        seen: Dict[str, List[str]] = {}
        for source in sources:
            if trigger in source.local_values:
                seen.setdefault(source.local_values[trigger], []).append(source.label)
        if not seen:
            continue
        if len(seen) == 1:
            (value,) = seen.keys()
            resolved[trigger] = value
        else:
            parts = "; ".join(
                f"{value!r} in {', '.join(labels)}" for value, labels in sorted(seen.items())
            )
            ambiguous[trigger] = (
                f"requiredIf trigger '{trigger}' has conflicting values across "
                f"registered sources: {parts}"
            )
    return resolved, ambiguous


def evaluate_union_completeness(
    schema: Dict[str, Any], sources: List[UnionSource]
) -> UnionCompletenessResult:
    """
    The core `completeness: union` evaluation (BL-030): is `schema`
    satisfied by the UNION of `sources`' presence, rather than requiring
    any single source to be self-sufficient?

    Only ever combines *presence* across sources, never values -- the one
    deliberate exception is a `requiredIf` trigger's own value, which is
    needed to decide whether a field is required at all (the same thing
    `schema_types.is_required_now` already reads from a single source
    today; this only extends *where* that one value may come from). A
    genuine cross-source conflict on a trigger's value is never resolved
    silently -- see `_resolve_union_requiredif_triggers`.

    `sources` must already be loaded and healthy -- a source that failed
    to load is this function's caller's concern (see `load_union_sources`
    and BL-030's explicit "a source failure must never be hidden by a
    successful union" requirement): this function has no way to know a
    source failed to load and must not be asked to guess.
    """
    union_trigger_values, ambiguous_triggers = _resolve_union_requiredif_triggers(
        schema, sources
    )
    any_unresolved_source = any(s.has_unresolved_source for s in sources)

    missing: set = set()
    blank: set = set()
    unresolved: set = set()
    invalid: Dict[str, str] = {}
    ambiguous_requiredif: Dict[str, str] = {}

    for key, details in schema.items():
        condition = details.get("requiredIf")
        trigger = condition.get("var") if isinstance(condition, dict) else None
        if trigger and trigger in ambiguous_triggers:
            ambiguous_requiredif[key] = ambiguous_triggers[trigger]
            continue

        status = _union_key_status(key, details, sources)
        if status.startswith("invalid:"):
            # Reported regardless of required-ness, matching
            # diff_against_schema's own unconditional single-source
            # validity check.
            invalid[key] = status[len("invalid:") :]
            continue

        if not schema_types.should_be_present(details, union_trigger_values):
            continue
        if status == "ok":
            continue
        if status == "blank":
            blank.add(key)
        elif any_unresolved_source:
            unresolved.add(key)
        else:
            missing.add(key)

    return UnionCompletenessResult(missing, blank, invalid, unresolved, ambiguous_requiredif)


def load_union_sources(
    service_name: str, container: Optional[str] = None
) -> Tuple[List[UnionSource], List[str]]:
    """
    Loads every one of a union-mode service's registered sources -- its
    local_file, plus every registered deployment manifest -- for
    `evaluate_union_completeness`. Reuses the exact same loading mechanism
    (`parsers.factory.get_parser` + `BaseParser.get_vars`) `check_schema`/
    `check_result` already use; not a second discovery/parsing path.

    Returns (successfully loaded sources, human-readable error messages for
    any source that failed to load). A source that fails to load
    contributes nothing to the union and is never silently treated as
    satisfying anything -- its failure is returned separately so a caller
    can surface it as an independent, un-hideable failure (BL-030's
    explicit source-health requirement), not folded into the completeness
    result itself.
    """
    sources: List[UnionSource] = []
    errors: List[str] = []

    paths = config_manager.get_env_paths(service_name=service_name)
    local_file = paths["local_file"]
    parser = get_parser(local_file, prefer=service_name)
    if not parser:
        errors.append(f"{local_file}: {_no_parser_found_message(local_file)}")
    else:
        try:
            local_values = parser.get_vars(local_file, get_values=True)
            sources.append(
                UnionSource(local_file, local_values, parser.has_unresolved_source)
            )
        except FileNotFoundError:
            errors.append(f"{local_file}: file not found.")
        except (ValueError, EnvShieldException) as e:
            errors.append(f"{local_file}: {e}")

    for manifest in config_manager.get_deployment_manifests(service_name):
        manifest_container = manifest.get("container") or container
        m_parser = get_parser(
            manifest["path"], container=manifest_container, prefer=service_name
        )
        if not m_parser:
            errors.append(f"{manifest['path']}: {_no_parser_found_message(manifest['path'])}")
            continue
        try:
            local_values = m_parser.get_vars(manifest["path"], get_values=True)
            sources.append(
                UnionSource(manifest["path"], local_values, m_parser.has_unresolved_source)
            )
        except (EnvShieldException, FileNotFoundError, ValueError) as e:
            errors.append(f"{manifest['path']}: {e}")

    return sources, errors


def union_completeness_result_to_dict(
    result: UnionCompletenessResult, source_errors: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Same result, as a plain JSON-serializable dict -- `check --json`'s
    `combined[<service>]` shape. `source_errors` (if any) always makes
    `clean` False, even when the sources that DID load happen to satisfy
    the schema on their own -- a source failure must never be hidden by an
    otherwise-successful union (BL-030's explicit requirement).
    """
    source_errors = source_errors or []
    return {
        "clean": result.is_clean and not source_errors,
        "missing": sorted(result.missing),
        "blank": sorted(result.blank),
        "invalid": dict(result.invalid),
        "unresolved": sorted(result.unresolved),
        "ambiguous_requiredif": dict(result.ambiguous_requiredif),
        "source_errors": list(source_errors),
    }


def _source_label(var: str, schema: Dict[str, Any]) -> str:
    """
    Describes a missing/blank variable's source for the report table --
    naming its default, if it has one, so the fix is obvious without a
    separate lookup. Never echoes the default for a 'secret' field: this
    table is normal CLI output, and a secret's default value must not
    appear there even though config_manager.load_schema already refuses
    to load a schema where this combination exists (defense in depth, same
    reasoning as schema_manager.sync_schema's own secret_default_conflict
    check).
    """
    field_schema = schema.get(var, {})
    default = field_schema.get("defaultValue")
    if field_schema.get("secret"):
        if default is not None:
            return "env.schema.toml (default: <hidden, secret>)"
        return "env.schema.toml (Required)"
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
        console.print(f"[red]Error:[/red] {_no_parser_found_message(file_path)}")
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
                "error": _no_parser_found_message(file_path),
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

    For a `completeness: union` service whose local_file is a Python
    module, this only appends a variable that's genuinely unsatisfied
    across *every* registered source -- not just this one -- so it can
    never re-introduce BL-030's original bug (appending a Compose-owned
    mode-switch variable into a Python secrets file). `.env.example`
    regeneration (the branch below) is unaffected by union mode: it's a
    generated documentation artifact that lists the whole contract
    regardless of which source actually satisfies each variable, which
    stays correct either way.
    """
    schema = config_manager.load_schema(service_name=service_name)
    paths = config_manager.get_env_paths(service_name=service_name)

    if paths["local_file"].endswith(".py"):
        already_satisfied = None
        if config_manager.get_service_completeness_mode(service_name) == "union":
            # Reuses load_union_sources -- the exact same loading path
            # check/doctor already use -- rather than a second, near-
            # duplicate "load every registered source" loop. A source that
            # fails to load here contributes nothing (fail-open, matching
            # this function's own existing additive/never-destructive
            # behavior): sync still appends whatever it covers, exactly as
            # it always has.
            other_sources, _ = load_union_sources(service_name)
            already_satisfied = {
                key
                for source in other_sources
                if source.label != paths["local_file"]
                for key, value in source.local_values.items()
                if value
            }
        return _sync_python_local_file(
            schema, paths["local_file"], already_satisfied=already_satisfied
        )

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

        # Surfaces contract metadata that's otherwise only visible by
        # opening env.schema.toml itself -- secret/enum/requiredIf are
        # exactly the facts a developer editing this template by hand
        # would need but currently can't see. enum values are escaped for
        # the same reason description/defaultValue are above; requiredIf's
        # comparison literal goes through schema_types.requiredif_condition_text
        # instead, which additionally withholds it if the triggering
        # variable is itself secret.
        annotations = []
        if details.get("secret"):
            annotations.append("secret")
        enum_values = schema_types.enum_values(details)
        if enum_values:
            safe_enum = ", ".join(
                v.replace("\r", "\\r").replace("\n", "\\n") for v in enum_values
            )
            annotations.append(f"enum: {safe_enum}")
        condition_text = schema_types.requiredif_condition_text(details, schema)
        if condition_text:
            annotations.append(f"required if {condition_text}")
        if annotations:
            body += f"# {'; '.join(annotations)}\n"

        # Defense in depth: config_manager.load_schema already refuses a
        # schema where this combination exists at all (see
        # SecretDefaultConflictError) -- this still never renders the
        # literal default for a secret field even if this function is
        # ever called with a schema dict that bypassed that gate.
        if schema_types.secret_default_conflict(details):
            default_value = ""
        else:
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


def _sync_python_local_file(
    schema: Dict[str, Any],
    local_file: str,
    already_satisfied: Optional[set] = None,
) -> bool:
    """
    Ensures a Python-module local config file declares every schema
    variable *this file is actually responsible for*. Returns whether it
    actually changed.

    `already_satisfied` (BL-030): schema keys to skip entirely, because a
    union-mode service's other registered sources already provide them --
    without this, a variable satisfied only by a Compose manifest would be
    force-appended into a Python secrets file that was never meant to
    declare it (the original BL-030 bug). None/empty for a non-union
    service -- every schema variable is appended exactly as before.
    """
    excluded = already_satisfied or set()

    if not os.path.exists(local_file):
        console.print(
            f"\n[bold]Creating [cyan]{local_file}[/cyan] from schema...[/bold]"
        )
        lines = [
            "# Auto-generated by 'envshield schema sync'.\n",
            "# Fill in real values below -- this file is your project's local config module.\n\n",
        ]
        for key, details in schema.items():
            if key in excluded:
                continue
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
        key: details
        for key, details in schema.items()
        if key not in existing_vars and key not in excluded
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
