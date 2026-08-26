# envshield/core/doctor.py
import os
import subprocess
import sys
from typing import List

import questionary
import typer
from rich.console import Console

from ..config import manager as config_manager
from ..parsers.factory import get_parser
from . import scanner, schema_manager, service_discovery, setup_manager
from .exceptions import EnvShieldException

console = Console()


class HealthCheck:
    def __init__(
        self, description: str, check_func, fix_func=None, fix_description: str = ""
    ):
        self.description = description
        self.check_func = check_func
        self.fix_func = fix_func
        self.fix_description = fix_description
        self.passed = False
        self.message = ""

    def run(self, fix: bool = False):
        try:
            self.passed, self.message = self.check_func()
        except EnvShieldException as e:
            self.passed, self.message = False, str(e)
        if not self.passed:
            console.print(f"[bold red]✗ {self.description}[/bold red]: {self.message}")
            if fix and self.fix_func:
                if questionary.confirm(f"{self.fix_description}").ask():
                    try:
                        self.fix_func()
                        self.passed, self.message = self.check_func()
                        if self.passed:
                            console.print("[bold green]✓ Fixed![/bold green]")
                    except EnvShieldException as e:
                        console.print(f"[bold red]Error during fix:[/bold red] {e}")

        else:
            console.print(f"[bold green]✓ {self.description}[/bold green]")
            # A passing check can still carry real information worth seeing
            # (e.g. "using schema default: LOG_LEVEL") -- --json already
            # includes it unconditionally; the plain Rich path silently
            # dropped it on success, which is exactly the kind of "green
            # checkmark hiding real information" this tool exists to avoid.
            if self.message:
                console.print(f"  [dim]{self.message}[/dim]")


def _check_config_files(service_name: str):
    config_exists = os.path.exists(config_manager.CONFIG_FILE_NAME)

    schema_path = config_manager.get_service_schema_path(service_name)
    schema_exists = bool(schema_path) and os.path.exists(schema_path)
    schema_label = schema_path or f"schema for service '{service_name}'"

    if not config_exists and not schema_exists:
        return (
            False,
            f"Neither '{config_manager.CONFIG_FILE_NAME}' nor '{schema_label}' found. "
            "Run 'envshield init' to create them.",
        )
    if not config_exists:
        return (
            False,
            f"Configuration file '{config_manager.CONFIG_FILE_NAME}' not found. "
            "Run 'envshield init' to create it.",
        )
    if not schema_exists:
        return (
            False,
            f"Schema file '{schema_label}' not found. Run 'envshield init' to create it.",
        )
    return True, "Found and accessible."


def _check_local_env_sync(service_name: str):
    try:
        schema = config_manager.load_schema(service_name=service_name)
        local_file = config_manager.get_env_paths(service_name=service_name)[
            "local_file"
        ]
        if not os.path.exists(local_file):
            return (
                False,
                f"Local env file '{local_file}' not found. Run 'envshield setup' to create it.",
            )

        parser = get_parser(local_file)
        if not parser:
            return False, f"Cannot parse local env file '{local_file}'."
        local_values = parser.get_vars(local_file, get_values=True)

        diff = schema_manager.diff_against_schema(schema, local_values)
        if diff.is_clean:
            return True, f"'{local_file}' is in sync with schema."
        return False, diff.summary()

    except EnvShieldException as e:
        # Preserve the real reason (already actionable -- see
        # config_manager's load_schema/get_service_schema_path) instead of
        # masking it behind a generic "could not load schema."
        return False, str(e)


def _check_deployment_manifest(service_name: str):
    manifests = config_manager.get_deployment_manifests(service_name)
    if not manifests:
        return True, "No deployment manifest registered -- nothing to check."

    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException as e:
        return False, str(e)

    all_clean = True
    messages = []
    for manifest in manifests:
        try:
            parser = get_parser(
                manifest["path"],
                container=manifest.get("container"),
                prefer=service_name,
            )
            if not parser:
                all_clean = False
                messages.append(
                    f"Cannot parse deployment manifest '{manifest['path']}'."
                )
                continue
            local_values = parser.get_vars(manifest["path"], get_values=True)

            diff = schema_manager.diff_against_schema(
                schema, local_values, has_unresolved_source=parser.has_unresolved_source
            )
            if diff.is_clean:
                messages.append(f"'{manifest['path']}' is in sync with schema.")
            else:
                all_clean = False
                messages.append(f"'{manifest['path']}': {diff.summary()}")
        except (EnvShieldException, FileNotFoundError, ValueError) as e:
            all_clean = False
            messages.append(f"Could not check '{manifest['path']}': {e}")

    return all_clean, "; ".join(messages)


def _check_example_file_sync(service_name: str):
    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException as e:
        return False, str(e)

    paths = config_manager.get_env_paths(service_name=service_name)
    local_file = paths["local_file"]
    example_file = paths["example_file"]

    # A Python-module local file (e.g. acme's env_config.local.py) has no
    # separate tracked template -- it IS the contract. 'Local Environment
    # Sync' already checks it declares every schema variable.
    if local_file.endswith(".py"):
        return (
            True,
            f"'{local_file}' has no separate template file (see 'Local Environment Sync').",
        )

    if not os.path.exists(example_file):
        return (
            False,
            f"'{example_file}' file is missing. Run 'envshield schema sync' to create it.",
        )

    schema_vars = set(schema.keys())
    parser = get_parser(example_file)
    if not parser:
        return False, f"Cannot parse '{example_file}'."
    example_vars = parser.get_vars(example_file)

    missing = schema_vars - example_vars
    extra = example_vars - schema_vars
    if not missing and not extra:
        return True, f"'{example_file}' is in sync with schema."

    messages = []
    if missing:
        messages.append(
            f"Missing from '{example_file}': {', '.join(missing)} "
            "(run 'envshield schema sync' to regenerate it)"
        )
    if extra:
        messages.append(
            f"Extra in '{example_file}': {', '.join(extra)} "
            "(remove them, or add them to the schema if they're meant to be there)"
        )
    return False, "; ".join(messages)


def _check_config_source_drift(service_name: str):
    """
    Catches the case a pinned config_source (see config_manager.add_service)
    exists specifically to prevent: a variable added to some OTHER real
    config source in the same directory (typically a Python config module
    gaining a var that a stale '.env' never picked up, since dotenv always
    wins the pinning priority) that never makes it into the schema, because
    nothing ever re-scans a source once it's pinned.
    """
    config_source = config_manager.get_service_config_source(service_name)
    if not config_source or not os.path.exists(config_source):
        return True, "No recorded config source to compare against."

    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException as e:
        return False, str(e)

    service_dir = config_manager.get_service_dir(service_name)
    other_sources = service_discovery.find_other_config_sources(
        service_dir, config_source
    )
    if not other_sources:
        return True, "No other config sources found to compare against."

    schema_vars = set(schema.keys())
    messages = []
    for source in other_sources:
        parser = get_parser(source)
        if not parser:
            continue
        try:
            source_vars = parser.get_vars(source)
        except (FileNotFoundError, OSError, EnvShieldException):
            # A malformed "other source" is skipped, not fatal to this
            # whole check -- otherwise one bad file would blank out every
            # other source's findings via HealthCheck.run()'s coarser outer
            # catch (see BL-002).
            continue
        extra = sorted(set(source_vars) - schema_vars)
        if extra:
            messages.append(
                f"'{source}' defines {', '.join(extra)}, not declared in the schema "
                f"(built from '{config_source}'). Run 'envshield import {source}' to add them."
            )

    if messages:
        return False, "; ".join(messages)
    return True, "No variables found in other sources beyond what's already declared."


def _check_legacy_configuration_keys(service_name: str):
    """
    Three keys that are silently never read, surfaced here rather than left
    silent: 'path:' (pre-4.5.0, renamed to 'schema:') and per-service
    'deployment_manifest:' (pre-4.2.0, moved to a top-level 'manifests:'
    list) are breaking, no-shim renames (see CHANGELOG); a per-service
    'manifests:' key was never valid at any version -- it's a natural but
    wrong guess, given 'schema:'/'local_file:' genuinely are per-service
    keys, for where the top-level 'manifests:' list belongs. All three
    leave whatever depended on them completely unvalidated with no error.
    """
    services = config_manager.get_services()
    service_config = services.get(service_name)
    if not isinstance(service_config, dict):
        return True, "No legacy configuration keys found."

    issues = []
    if "path" in service_config and "schema" not in service_config:
        issues.append(
            "uses the legacy 'path:' key -- rename it to 'schema:' in envshield.yml (renamed in 4.5.0)"
        )
    if "deployment_manifest" in service_config:
        issues.append(
            "uses the legacy per-service 'deployment_manifest:' key -- move it into a top-level "
            "'manifests:' entry in envshield.yml (moved in 4.2.0); until then, no deployment "
            "manifest is being validated for this service"
        )
    if "manifests" in service_config:
        issues.append(
            "has a per-service 'manifests:' key -- deployment manifests are only ever read from "
            "a TOP-LEVEL 'manifests:' list in envshield.yml, never nested under a service; this "
            "key is never read, so no manifest is being validated for this service"
        )

    if issues:
        return False, "; ".join(issues)
    return True, "No legacy configuration keys found."


def _check_config_source_reads_environment(service_name: str):
    """
    'check'/'doctor' can only ever compare '.env' against the schema --
    never against the code that's supposed to consume it. A Python
    config_source that's pure hardcoded literals (no 'os.environ'/
    'os.getenv' anywhere) can pass every other check forever while the
    running app never actually reads '.env' at all. Only meaningful for a
    Python config_source: a dotenv config_source *is* the environment
    values, nothing to read from itself.
    """
    config_source = config_manager.get_service_config_source(service_name)
    if not config_source or not config_source.endswith(".py"):
        return True, "Not a Python config source -- nothing to check."
    if not os.path.exists(config_source):
        return True, "Recorded config source no longer exists."

    if service_discovery.looks_like_it_reads_the_environment(config_source):
        return True, f"'{config_source}' reads from the environment."

    return (
        False,
        f"'{config_source}' has hardcoded values -- nothing in it reads from the "
        "environment (no 'os.environ'/'os.getenv'), so nothing guarantees your app "
        "actually consumes '.env' at all. Run 'envshield generate' and import from "
        "its output instead.",
    )


def _check_git_hooks():
    """
    Checks both hooks, not just pre-commit -- post-merge is the one thing
    that actually tells a developer about a schema change they pulled
    without any action on their part, so its absence deserves the exact
    same visibility pre-commit's already had. A hook file existing isn't
    enough either; it has to actually be EnvShield's (the shared marker
    every generated hook script carries), the same bar pre-commit already
    held itself to.
    """
    if not scanner.git_utils.get_git_root():
        return False, "Not a Git repository."

    hooks_dir = scanner.git_utils.get_hooks_dir()
    missing = []
    for hook_name in ("pre-commit", "post-merge"):
        hook_path = os.path.join(hooks_dir, hook_name)
        if not os.path.exists(hook_path):
            missing.append(hook_name)
            continue
        with open(hook_path, "r") as f:
            content = f.read()
        if scanner.ENVSHIELD_HOOK_MARKER not in content:
            missing.append(hook_name)

    if missing:
        return (
            False,
            f"Not installed (or not EnvShield's): {', '.join(missing)}. "
            "Run 'envshield hook install' to fix.",
        )
    return True, "pre-commit and post-merge hooks are both installed and active."


def _run_init_fix() -> None:
    """
    Runs 'envshield init' as a fix step, via the current interpreter rather
    than a bare 'envshield' shell lookup -- so it can't silently no-op just
    because the console script isn't on PATH in whatever shell/venv 'doctor'
    happened to be run from. Inherits stdio (like the previous os.system call)
    so init's own interactive prompts still work; a non-zero exit is reported
    instead of being swallowed.
    """
    result = subprocess.run([sys.executable, "-m", "envshield", "init"], check=False)
    if result.returncode != 0:
        raise EnvShieldException(
            f"'envshield init' exited with code {result.returncode}."
        )


def _build_checks(service_name: str) -> List[HealthCheck]:
    """
    The list of health checks to run, shared by run_health_check (Rich
    rendering + --fix) and run_health_check_json (--json) so they can never
    quietly diverge on which checks exist.
    """
    checks: List[HealthCheck] = [
        HealthCheck(
            "Configuration Files",
            lambda: _check_config_files(service_name),
            fix_func=_run_init_fix,
            fix_description="No config found. Run 'envshield init' to create them?",
        ),
        HealthCheck(
            "Local Environment Sync",
            lambda: _check_local_env_sync(service_name),
            # Delegates to the same wizard 'setup' already runs, rather than
            # re-implementing "prompt for whatever's missing/blank/invalid"
            # here -- it already re-validates existing values (not just
            # presence) and leaves everything already-correct untouched.
            fix_func=lambda: setup_manager.run_setup(service_name=service_name),
            fix_description="Run the setup wizard to fill in missing/invalid values?",
        ),
        HealthCheck(
            "Template Sync",
            lambda: _check_example_file_sync(service_name),
            fix_func=lambda: schema_manager.sync_schema(service_name=service_name),
            fix_description="Template is missing or out of sync. Generate/update it from the schema?",
        ),
        HealthCheck(
            "Git Hooks",
            _check_git_hooks,
            fix_func=lambda: (
                scanner.install_pre_commit_hook(force=True),
                scanner.install_post_merge_hook(force=True),
            ),
            fix_description="One or both git hooks are missing or not EnvShield's. Install them now?",
        ),
    ]

    # Only shown at all when the service's envshield.yml entry actually
    # carries one of the legacy keys -- otherwise this would print a
    # permanent, always-green, always-identical line for every project on
    # every run forever, for a condition that essentially never applies.
    service_config = config_manager.get_services().get(service_name)
    if isinstance(service_config, dict) and (
        ("path" in service_config and "schema" not in service_config)
        or "deployment_manifest" in service_config
        or "manifests" in service_config
    ):
        checks.append(
            HealthCheck(
                "Legacy Configuration Keys",
                lambda: _check_legacy_configuration_keys(service_name),
                fix_func=None,
            )
        )

    # Only shown at all when a config_source was actually recorded -- an
    # older envshield.yml or a schema built from a generic template has
    # nothing to compare drift against.
    if config_manager.get_service_config_source(service_name):
        checks.append(
            HealthCheck(
                "Config Source Drift",
                lambda: _check_config_source_drift(service_name),
                fix_func=None,
            )
        )

    # Only shown at all for a Python config_source -- a dotenv one has
    # nothing separate to "read the environment," it IS the values.
    config_source = config_manager.get_service_config_source(service_name)
    if config_source and config_source.endswith(".py"):
        checks.append(
            HealthCheck(
                "Config Source Reads Environment",
                lambda: _check_config_source_reads_environment(service_name),
                fix_func=None,
            )
        )

    # Only shown at all when a manifest is actually registered for this
    # service -- a project that doesn't use one shouldn't see a check for
    # it every single run.
    if config_manager.get_deployment_manifests(service_name):
        checks.append(
            HealthCheck(
                "Deployment Manifest",
                lambda: _check_deployment_manifest(service_name),
                fix_func=None,
            )
        )

    return checks


def run_health_check(fix: bool, service_name: str):
    """Runs a suite of health checks on one service's EnvShield setup."""
    console.print("\n[bold cyan]Running EnvShield Health Check...[/bold cyan]")

    checks = _build_checks(service_name)

    all_passed = True
    for check in checks:
        check.run(fix=fix)
        if not check.passed:
            all_passed = False

    console.print("\n[bold]--------------------[/bold]")
    if all_passed:
        console.print(
            "[bold green]✨ Health check complete. Everything looks great! ✨[/bold green]"
        )
    else:
        console.print(
            "[bold yellow]Health check complete. Some issues were found.[/bold yellow]"
        )
        raise typer.Exit(code=1)


def run_health_check_json(service_name: str) -> dict:
    """
    Same checks as run_health_check, collected as a plain, JSON-serializable
    dict instead of Rich output -- for '--json'. Never offers --fix: an
    interactive confirm prompt makes no sense in a machine-readable mode.
    """
    results = []
    all_passed = True
    for check in _build_checks(service_name):
        try:
            passed, message = check.check_func()
        except EnvShieldException as e:
            passed, message = False, str(e)
        if not passed:
            all_passed = False
        results.append(
            {"name": check.description, "passed": passed, "message": message}
        )

    return {"service": service_name, "passed": all_passed, "checks": results}
