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
from . import scanner, schema_manager, setup_manager
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


def _check_config_files(service_name: str):
    config_exists = os.path.exists(config_manager.CONFIG_FILE_NAME)

    schema_path = config_manager.get_service_schema_path(service_name)
    schema_exists = bool(schema_path) and os.path.exists(schema_path)
    schema_label = schema_path or f"schema for service '{service_name}'"

    if not config_exists and not schema_exists:
        return (
            False,
            f"Neither '{config_manager.CONFIG_FILE_NAME}' nor '{schema_label}' found.",
        )
    if not config_exists:
        return (
            False,
            f"Configuration file '{config_manager.CONFIG_FILE_NAME}' not found.",
        )
    if not schema_exists:
        return False, f"Schema file '{schema_label}' not found."
    return True, "Found and accessible."


def _check_local_env_sync(service_name: str):
    try:
        schema = config_manager.load_schema(service_name=service_name)
        local_file = config_manager.get_env_paths(service_name=service_name)[
            "local_file"
        ]
        if not os.path.exists(local_file):
            return False, f"Local env file '{local_file}' not found."

        parser = get_parser(local_file)
        if not parser:
            return False, f"Cannot parse local env file '{local_file}'."
        local_values = parser.get_vars(local_file, get_values=True)

        diff = schema_manager.diff_against_schema(schema, local_values)
        if diff.is_clean:
            return True, f"'{local_file}' is in sync with schema."
        return False, diff.summary()

    except EnvShieldException:
        return False, "Could not load schema to perform check."


def _check_deployment_manifest(service_name: str):
    manifests = config_manager.get_deployment_manifests(service_name)
    if not manifests:
        return True, "No deployment manifest registered -- nothing to check."

    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException:
        return False, "Could not load schema to perform check."

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

            diff = schema_manager.diff_against_schema(schema, local_values)
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
    except EnvShieldException:
        return False, "Could not load schema to perform sync check."

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
        return False, f"'{example_file}' file is missing."

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
        messages.append(f"Missing from '{example_file}': {', '.join(missing)}")
    if extra:
        messages.append(f"Extra in '{example_file}': {', '.join(extra)}")
    return False, "; ".join(messages)


def _check_git_hook():
    if not scanner.git_utils.get_git_root():
        return False, "Not a Git repository."

    hooks_dir = scanner.git_utils.get_hooks_dir()
    hook_path = os.path.join(hooks_dir, "pre-commit")
    if not os.path.exists(hook_path):
        return False, "Pre-commit hook is not installed."

    with open(hook_path, "r") as f:
        content = f.read()
        if "envshield scan --staged" not in content:
            return False, "Pre-commit hook is present but does not run EnvShield."

    return True, "Pre-commit hook is installed and active."


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
            "Git Pre-commit Hook",
            _check_git_hook,
            fix_func=lambda: scanner.install_pre_commit_hook(force=True),
            fix_description="The security hook is not installed. Install it now?",
        ),
    ]

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
    console.print("\n[bold cyan]🛡️  Running EnvShield Health Check...[/bold cyan]")

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
