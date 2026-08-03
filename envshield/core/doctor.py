# envshield/core/doctor.py
import os
from typing import List, Optional

import questionary
import typer
from rich.console import Console

from . import schema_manager, scanner
from .exceptions import EnvShieldException
from ..config import manager as config_manager
from ..parsers.factory import get_parser

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
        self.passed, self.message = self.check_func()
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


def _check_config_files(service_name: Optional[str] = None):
    config_exists = os.path.exists(config_manager.CONFIG_FILE_NAME)

    if service_name:
        schema_path = config_manager.get_service_schema_path(service_name)
        schema_exists = bool(schema_path) and os.path.exists(schema_path)
        schema_label = schema_path or f"schema for service '{service_name}'"
    else:
        schema_exists = os.path.exists(config_manager.SCHEMA_FILE_NAME)
        schema_label = config_manager.SCHEMA_FILE_NAME

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


def _check_local_env_sync(service_name: Optional[str] = None):
    try:
        schema = config_manager.load_schema(service_name=service_name)
        schema_vars = set(schema.keys())
        required_vars = {
            key for key, details in schema.items() if "defaultValue" not in details
        }
        local_file = config_manager.get_env_paths(service_name=service_name)["local_file"]
        if not os.path.exists(local_file):
            return False, f"Local env file '{local_file}' not found."

        parser = get_parser(local_file)
        if not parser:
            return False, f"Cannot parse local env file '{local_file}'."
        local_values = parser.get_vars(local_file, get_values=True)
        local_vars = set(local_values.keys())

        missing = schema_vars - local_vars
        # A required var (no schema default) present only as a blank
        # placeholder -- e.g. `SECRETS_ENCRYPTION_KEY = ""` checked in ahead
        # of a real per-developer secret -- is just as incomplete as one
        # missing outright. Checking only for the key's presence let this
        # go undetected right up until it broke at runtime.
        blank_required = {
            key for key in required_vars if key in local_values and not local_values[key]
        }
        extra = local_vars - schema_vars

        if not missing and not blank_required and not extra:
            return True, f"'{local_file}' is in sync with schema."

        messages = []
        if missing:
            messages.append(f"Missing variables: {', '.join(sorted(missing))}")
        if blank_required:
            messages.append(f"Required but blank: {', '.join(sorted(blank_required))}")
        if extra:
            messages.append(f"Extra variables: {', '.join(sorted(extra))}")
        return False, "; ".join(messages)

    except EnvShieldException:
        return False, "Could not load schema to perform check."


def _check_example_file_sync(service_name: Optional[str] = None):
    try:
        schema = config_manager.load_schema(service_name=service_name)
    except EnvShieldException:
        return False, "Could not load schema to perform sync check."

    paths = config_manager.get_env_paths(service_name=service_name)
    local_file = paths["local_file"]
    example_file = paths["example_file"]

    # A Python-module local file (e.g. zeus's env_config.local.py) has no
    # separate tracked template -- it IS the contract. 'Local Environment
    # Sync' already checks it declares every schema variable.
    if local_file.endswith(".py"):
        return True, f"'{local_file}' has no separate template file (see 'Local Environment Sync')."

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

    hook_path = os.path.join(
        scanner.git_utils.get_git_root(), ".git", "hooks", "pre-commit"
    )
    if not os.path.exists(hook_path):
        return False, "Pre-commit hook is not installed."

    with open(hook_path, "r") as f:
        content = f.read()
        if "envshield scan --staged" not in content:
            return False, "Pre-commit hook is present but does not run EnvShield."

    return True, "Pre-commit hook is installed and active."


def run_health_check(fix: bool, service_name: Optional[str] = None):
    """
    Runs a suite of health checks on the project's EnvShield setup.

    If `service_name` is provided, checks that specific service's setup.
    Otherwise, checks the root setup.
    """
    console.print("\n[bold cyan]🛡️  Running EnvShield Health Check...[/bold cyan]")

    checks: List[HealthCheck] = [
        HealthCheck(
            "Configuration Files",
            lambda: _check_config_files(service_name),
            fix_func=lambda: os.system("envshield init"),
            fix_description="No config found. Run 'envshield init' to create them?",
        ),
        HealthCheck(
            "Local Environment Sync",
            lambda: _check_local_env_sync(service_name),
            fix_func=None,
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
