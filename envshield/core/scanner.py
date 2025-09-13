# envshield/core/scanner.py
import fnmatch
import os
import re
import stat
from typing import Dict, List, Optional

import questionary
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config import manager as config_manager
from ..core.exceptions import EnvShieldException
from ..utils import git_utils

console = Console()

SECRET_PATTERNS: List[Dict[str, str]] = [
    {
        "name": "Generic API Key",
        "pattern": r"(?i)(key|api(?!version)|token|secret|password|auth|credential)[a-z0-9_ .\-,]{0,25}\s*[:=]\s*['\"]([0-9a-zA-Z\-_=]{16,64})['\"]",
    },
    {
        "name": "Private Key",
        "pattern": r"-----BEGIN (?:EC|PGP|DSA|RSA|OPENSSH|ENCRYPTED)? ?PRIVATE KEY(?: BLOCK)?-----",
    },
    {
        "name": "JSON Web Token (JWT)",
        "pattern": r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    },
    {
        "name": "Database Connection String",
        "pattern": r"(?i)(postgres|mysql|mongodb(?:\+srv)?|redis)://[^:]+:[^@]+@",
    },
    {
        "name": "AWS Access Key ID",
        "pattern": r"\b(A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b",
    },
    {
        "name": "AWS Secret Access Key",
        "pattern": r"(?i)aws(.{0,20})?['\"][0-9a-zA-Z\/+=]{40}['\"]",
    },
    {"name": "Google Cloud API Key", "pattern": r"\bAIza[0-9A-Za-z\-_]{35}\b"},
    {"name": "Google OAuth Access Token", "pattern": r"\bya29\.[0-9A-Za-z\-_]+\b"},
    {
        "name": "GitHub Personal Access Token (Classic)",
        "pattern": r"\bghp_[0-9a-zA-Z]{36}\b",
    },
    {
        "name": "GitHub Personal Access Token (Fine-grained)",
        "pattern": r"\bgithub_pat_[0-9a-zA-Z]{22}_[0-9a-zA-Z]{59}\b",
    },
    {"name": "GitHub OAuth Access Token", "pattern": r"\bgho_[0-9a-zA-Z]{36}\b"},
    {"name": "GitHub App Token", "pattern": r"\b(ghu|ghs)_[0-9a-zA-Z]{36}\b"},
    {
        "name": "Terraform Cloud/Enterprise Token",
        "pattern": r"\b[a-zA-Z0-9]+\.atlasv1\.[a-zA-Z0-9\-_=]{60,70}\b",
    },
    {"name": "Slack Token", "pattern": r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b"},
    {"name": "Telegram Bot Token", "pattern": r"\b[0-9]{8,10}:[a-zA-Z0-9_-]{35}\b"},
    {"name": "Twilio API Key", "pattern": r"\bSK[0-9a-fA-F]{32}\b"},
    {
        "name": "SendGrid API Key",
        "pattern": r"\bSG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}\b",
    },
    {"name": "Mailchimp API Key", "pattern": r"\b[0-9a-f]{32}-us[0-9]{1,2}\b"},
    {"name": "Mailgun API Key", "pattern": r"\bkey-[0-9a-zA-Z]{32}\b"},
    {
        "name": "Stripe API Key",
        "pattern": r"\b(sk|pk)_(test|live)_[0-9a-zA-Z]{24,99}\b",
    },
    {
        "name": "Heroku API Key",
        "pattern": r"(?i)heroku[a-z0-9_\- ]*['\"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['\"]",
    },
    {
        "name": "Discord Bot Token",
        "pattern": r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}\b",
    },
    {"name": "npm Token", "pattern": r"\bnpm_[a-zA-Z0-9]{36}\b"},
    {
        "name": "PyPI Upload Token",
        "pattern": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9-_]{50,1000}\b",
    },
]
USAGE_PATTERNS: List[Dict[str, str]] = [
    {
        "name": "Python os.environ.get",
        "pattern": r"os\.environ\.get\s*\(\s*['\"](\w+)['\"]",
    },
    {"name": "Python os.getenv", "pattern": r"os\.getenv\s*\(\s*['\"](\w+)['\"]"},
    {"name": "Node.js process.env", "pattern": r"process\.env\.(\w+)"},
]


def _scan_single_file(file_path: str, schema_vars: set) -> (List[Dict], List[Dict]):
    """
    Helper to scan one file for both secrets and undeclared variables.
    Returns two lists: one for secrets, one for undeclared variables.
    """
    secret_findings = []
    undeclared_findings = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                # Check for secrets
                for secret in SECRET_PATTERNS:
                    if re.search(secret["pattern"], line):
                        secret_findings.append(
                            {
                                "file_path": file_path,
                                "line_num": line_num,
                                "secret_type": secret["name"],
                                "line_content": line.strip(),
                            }
                        )
                        break

                # Check for undeclared variables
                for usage in USAGE_PATTERNS:
                    matches = re.findall(usage["pattern"], line)
                    for var_name in matches:
                        if var_name not in schema_vars:
                            undeclared_findings.append(
                                {
                                    "file_path": file_path,
                                    "line_num": line_num,
                                    "variable_name": var_name,
                                }
                            )

    except (IOError, OSError):
        return [], []

    return secret_findings, undeclared_findings


def _collect_files_to_scan(paths: Optional[List[str]], staged_only: bool) -> List[str]:
    """Collects a list of files to be scanned based on user input."""

    if staged_only:
        console.print("Scanning [yellow]staged files[/yellow]...")
        files = git_utils.get_staged_files()
        if not files:
            console.print("[green]No staged files to scan.[/green]")
            raise typer.Exit()
        return files

    files_to_scan = []
    scan_paths = paths or ["."]

    if "." in scan_paths:
        console.print("Scanning [yellow]current directory[/yellow] recursively...")

    for path in scan_paths:
        if os.path.isfile(path):
            files_to_scan.append(os.path.abspath(path))
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                for file in files:
                    files_to_scan.append(os.path.join(root, file))
    return files_to_scan


def _filter_files(files: List[str], exclude_patterns: List[str]) -> List[str]:
    """Filters a list of files against a list of glob patterns."""
    final_files = []
    for file_path in files:
        is_excluded = False
        normalized_path = file_path.replace(os.getcwd() + os.sep, "")
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(normalized_path, pattern):
                is_excluded = True
                break
        if not is_excluded:
            final_files.append(file_path)
    return final_files


def run_scan(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
):
    """The main function to orchestrate the scanning process."""
    all_exclusions = []
    try:
        config = config_manager.load_config(config_path)
        config_exclusions = config.get("secret_scanning", {}).get("exclude_files", [])
        all_exclusions.extend(config_exclusions)
    except EnvShieldException:
        pass

    if exclude_patterns:
        all_exclusions.extend(exclude_patterns)

    try:
        schema = config_manager.load_schema()
        schema_vars = set(schema.keys())
        console.print("[dim]Schema loaded for compliance check.[/dim]")
    except EnvShieldException:
        schema_vars = set()
        console.print(
            "[yellow]Warning: Schema not found. Skipping undeclared variable check.[/yellow]"
        )

    files_to_scan = _collect_files_to_scan(paths, staged_only)
    final_files_to_scan = _filter_files(files_to_scan, all_exclusions)

    all_secret_findings = []
    all_undeclared_findings = []

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("Scanning [cyan]{task.description}[/cyan]"),
        console=console,
    ) as progress:
        scan_task = progress.add_task("files...", total=len(final_files_to_scan))
        for file_path in final_files_to_scan:
            progress.update(
                scan_task, description=os.path.basename(file_path), advance=1
            )
            if os.path.exists(file_path) and os.path.getsize(file_path) > 1_000_000:
                continue
            secrets, undeclared = _scan_single_file(file_path, schema_vars)
            all_secret_findings.extend(secrets)
            all_undeclared_findings.extend(undeclared)

    found_issues = False
    if all_secret_findings:
        found_issues = True
        console.print(
            f"\n[bold red]🚨 DANGER: Found {len(all_secret_findings)} potential secret(s)![/bold red]"
        )
        table = Table(title="Secret Scan Results", border_style="red")
        table.add_column("File", style="cyan")
        table.add_column("Line", style="yellow")
        table.add_column("Secret Type", style="magenta")
        table.add_column("Line Content", style="white")
        for finding in all_secret_findings:
            table.add_row(
                finding["file_path"],
                str(finding["line_num"]),
                finding["secret_type"],
                finding["line_content"],
            )
        console.print(table)

    if all_undeclared_findings:
        found_issues = True
        console.print(
            f"\n[bold yellow]⚠️  WARNING: Found {len(all_undeclared_findings)} undeclared variable(s)![/bold yellow]"
        )
        undeclared_table = Table(
            title="Undeclared Variable Usage", border_style="yellow"
        )
        undeclared_table.add_column("File", style="cyan")
        undeclared_table.add_column("Line", style="yellow")
        undeclared_table.add_column("Variable Name", style="white")
        for finding in all_undeclared_findings:
            undeclared_table.add_row(
                finding["file_path"],
                str(finding["line_num"]),
                finding["variable_name"],
            )
        console.print(undeclared_table)
        console.print(
            "\n[bold]Suggestion:[/bold] Please add these variables to your 'env.schema.toml' to maintain your configuration contract."
        )

    if not found_issues:
        console.print(
            "\n[bold green]✓ No issues found. Your configuration is secure and compliant![/bold green]"
        )
        return

    if staged_only:
        console.print(
            "\n[bold red]Commit aborted. Please fix the issues above before committing.[/bold red]"
        )

    raise typer.Exit(code=1)


def install_pre_commit_hook(force: bool = False, non_interactive: bool = False):
    """Installs the Git pre-commit hook."""
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository. Cannot install hook.")

    hooks_dir = os.path.join(git_root, ".git", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")

    hook_script_content = (
        "#!/bin/sh\n\n"
        "# Hook installed by EnvShield\n"
        "# This hook scans for hardcoded secrets AND undeclared environment variables.\n"
        "envshield scan --staged\n"
    )

    try:
        if os.path.exists(pre_commit_path):
            if non_interactive:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A pre-commit hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield scan --staged' to your existing hook script."
                )
                return

            if not force:
                overwrite = questionary.confirm(
                    "A pre-commit hook already exists. Do you want to overwrite it?",
                    default=False,
                ).ask()
                if not overwrite:
                    console.print("[yellow]Hook installation cancelled.[/yellow]")
                    raise typer.Exit()

        with open(pre_commit_path, "w") as f:
            f.write(hook_script_content)

        current_permissions = os.stat(pre_commit_path).st_mode
        os.chmod(
            pre_commit_path,
            current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
        )

        console.print(
            "[bold green]✓ Git pre-commit hook installed successfully![/bold green]"
        )

    except (IOError, OSError) as e:
        raise EnvShieldException(
            f"Failed to write or set permissions for the hook file: {e}"
        )
    except (TypeError, KeyboardInterrupt):
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()
