# envshield/core/doctor.py
import os
from typing import List

import questionary
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


def _check_config_files():
    config_exists = os.path.exists(config_manager.CONFIG_FILE_NAME)
    schema_exists = os.path.exists(config_manager.SCHEMA_FILE_NAME)
    if not config_exists and not schema_exists:
        return (
            False,
            f"Neither '{config_manager.CONFIG_FILE_NAME}' nor '{config_manager.SCHEMA_FILE_NAME}' found.",
        )
    if not config_exists:
        return (
            False,
            f"Configuration file '{config_manager.CONFIG_FILE_NAME}' not found.",
        )
    if not schema_exists:
        return False, f"Schema file '{config_manager.SCHEMA_FILE_NAME}' not found."
    return True, "Found and accessible."


def _check_local_env_sync():
    try:
        schema = config_manager.load_schema()
        schema_vars = set(schema.keys())
        local_file = ".env"
        if not os.path.exists(local_file):
            return False, f"Local env file '{local_file}' not found."

        parser = get_parser(local_file)
        if not parser:
            return False, "Cannot parse local env file."
        local_vars = parser.get_vars(local_file)

        missing = schema_vars - local_vars
        extra = local_vars - schema_vars

        if not missing and not extra:
            return True, "Local '.env' is in sync with schema."

        messages = []
        if missing:
            messages.append(f"Missing variables: {', '.join(missing)}")
        if extra:
            messages.append(f"Extra variables: {', '.join(extra)}")
        return False, "; ".join(messages)

    except EnvShieldException:
        return False, "Could not load schema to perform check."


def _check_example_file_sync():
    try:
        if not os.path.exists(".env.example"):
            return False, "'.env.example' file is missing."
        return True, "'.env.example' exists."
    except EnvShieldException:
        return False, "Could not load schema to perform sync check."


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


def run_health_check(fix: bool):
    """
    Runs a suite of health checks on the project's EnvShield setup.
    """
    console.print("\n[bold cyan]🛡️  Running EnvShield Health Check...[/bold cyan]")

    checks: List[HealthCheck] = [
        HealthCheck(
            "Configuration Files",
            _check_config_files,
            fix_func=lambda: os.system("envshield init"),
            fix_description="No config found. Run 'envshield init' to create them?",
        ),
        HealthCheck("Local Environment Sync", _check_local_env_sync, fix_func=None),
        HealthCheck(
            "Example File Sync",
            _check_example_file_sync,
            fix_func=schema_manager.sync_schema,
            fix_description="'.env.example' is missing or out of sync. Generate it from the schema?",
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
