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
        # Only the 'sk_' (secret) prefix -- 'pk_' is Stripe's *publishable*
        # key, explicitly meant to ship in client-side code (e.g. a
        # NEXT_PUBLIC_/VITE_-prefixed var). Flagging it as a secret was a
        # false positive on exactly the values that are supposed to be public.
        "name": "Stripe Secret Key",
        "pattern": r"\bsk_(test|live)_[0-9a-zA-Z]{24,99}\b",
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

# Directories that are never useful to scan and are expensive/noisy to walk:
# dependency trees, VCS internals, virtualenvs, and build artifacts. These are
# always pruned in addition to whatever the user configures in envshield.yml.
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
}


def _is_default_excluded_dir(dirname: str) -> bool:
    return (
        dirname in DEFAULT_EXCLUDED_DIRS
        or dirname.endswith(".egg-info")
        or dirname.endswith(".dist-info")
    )


def _scan_single_file(
    file_path: str, schema_vars: set, content: Optional[str] = None
) -> (List[Dict], List[Dict]):
    """
    Helper to scan one file for both secrets and undeclared variables.
    Returns two lists: one for secrets, one for undeclared variables.

    If `content` is provided, it's scanned directly instead of reading the
    file from disk — used for `--staged` scans, where we must scan what's
    actually staged in the Git index, not the working-tree copy (which can
    differ, e.g. if a secret was staged and then edited out without
    re-staging).
    """
    secret_findings = []
    undeclared_findings = []

    try:
        if content is not None:
            lines = content.splitlines(keepends=True)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
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
            for root, dirs, files in os.walk(path):
                # Prune in-place so os.walk doesn't descend into these dirs at all.
                dirs[:] = [d for d in dirs if not _is_default_excluded_dir(d)]
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


def _normalize_for_dir_match(file_path: str) -> str:
    """Best-effort normalization to a cwd-relative path, for comparing a scanned file against a service directory."""
    if os.path.isabs(file_path):
        try:
            file_path = os.path.relpath(file_path, os.getcwd())
        except ValueError:
            pass
    return os.path.normpath(file_path)


def _build_undeclared_var_resolver(service_name: Optional[str]):
    """
    Returns a function mapping a scanned file path to the schema variable
    set it should be checked against for undeclared-variable detection --
    or None if there's no schema at all to check against.

    An explicit `service_name` (or a single-service/root project) checks
    every file against that one schema -- unchanged, single-target
    behavior.

    Otherwise, for a multi-service project scanned without --service, each
    configured service's own schema is matched against files under that
    service's own directory (the directory its schema lives in) -- a file
    under 'athena/' is checked against athena's schema, not hermes's.
    Files outside every service's directory fall back to the root schema,
    if one exists. Without this, running the pre-commit hook's plain
    `envshield scan --staged` (no --service) on a multi-service project
    would look for a root 'env.schema.toml' that was never there, and
    silently skip the undeclared-variable check for every service.
    """
    if service_name or not config_manager.is_multi_service():
        try:
            schema_vars = set(config_manager.load_schema(service_name=service_name).keys())
            console.print("[dim]Schema loaded for compliance check.[/dim]")
        except EnvShieldException:
            console.print(
                "[yellow]Warning: Schema not found. Skipping undeclared variable check.[/yellow]"
            )
            return None
        return lambda _file_path: schema_vars

    service_dirs = []
    for name in sorted(config_manager.get_services().keys()):
        try:
            service_dir = _normalize_for_dir_match(config_manager.get_service_dir(name))
            schema_vars = set(config_manager.load_schema(service_name=name).keys())
        except EnvShieldException:
            continue
        service_dirs.append((service_dir, schema_vars))
    # Longest directory first, so a nested service dir wins over a shorter sibling.
    service_dirs.sort(key=lambda item: len(item[0]), reverse=True)

    try:
        root_vars = set(config_manager.load_schema().keys())
    except EnvShieldException:
        root_vars = set()

    if not service_dirs and not root_vars:
        console.print(
            "[yellow]Warning: No schema found for any configured service. Skipping undeclared variable check.[/yellow]"
        )
        return None

    console.print("[dim]Per-service schemas loaded for compliance check.[/dim]")

    def _resolve(file_path: str) -> set:
        normalized = _normalize_for_dir_match(file_path)
        for service_dir, schema_vars in service_dirs:
            if normalized == service_dir or normalized.startswith(service_dir + os.sep):
                return schema_vars
        return root_vars

    return _resolve


def run_scan(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
):
    """
    The main function to orchestrate the scanning process.

    If `service_name` is provided, scans for variables against that service's schema.
    Otherwise, on a multi-service project, each file is checked against
    whichever service's schema its directory belongs to.
    """
    all_exclusions = []
    try:
        config = config_manager.load_config(config_path)
        config_exclusions = config.get("secret_scanning", {}).get("exclude_files", [])
        all_exclusions.extend(config_exclusions)
    except EnvShieldException:
        pass

    if exclude_patterns:
        all_exclusions.extend(exclude_patterns)

    schema_resolver = _build_undeclared_var_resolver(service_name)

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

            schema_vars = schema_resolver(file_path) if schema_resolver else set()

            if staged_only:
                # Scan what's actually staged in the index, not the working-tree
                # copy on disk -- they can differ (see get_staged_file_content).
                content = git_utils.get_staged_file_content(file_path)
                if content is None or len(content) > 1_000_000:
                    continue
                secrets, undeclared = _scan_single_file(
                    file_path, schema_vars, content=content
                )
            else:
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


def _generate_post_merge_hook_content() -> str:
    """
    Generates the bash script content for the post-merge hook.
    Dynamically detects single-service vs multi-service projects.
    """
    config = config_manager.load_config()

    if config.get("services"):
        # Multi-service project: generate a doctor call for each service
        services = list(config["services"].keys())
        service_checks = "\n  ".join(
            f'envshield doctor --service {svc} 2>/dev/null'
            for svc in services
        )
        return (
            "#!/bin/sh\n\n"
            "# Hook installed by EnvShield\n"
            "# This hook checks environment configuration after pulling changes.\n"
            "# If new required variables were added, it alerts the developer immediately.\n"
            "# Non-blocking: warns but doesn't fail the merge.\n\n"
            f"  {service_checks}\n\n"
            "exit 0\n"
        )
    else:
        # Single-service project: check the root schema
        return (
            "#!/bin/sh\n\n"
            "# Hook installed by EnvShield\n"
            "# This hook checks environment configuration after pulling changes.\n"
            "# If new required variables were added, it alerts the developer immediately.\n"
            "# Non-blocking: warns but doesn't fail the merge.\n\n"
            "envshield doctor 2>/dev/null\n\n"
            "exit 0\n"
        )


def install_post_merge_hook(force: bool = False, non_interactive: bool = False):
    """
    Installs a Git post-merge hook that runs 'envshield doctor' after pulling changes.
    This ensures developers are alerted immediately if a pulled commit adds a new required
    environment variable, without waiting for a container restart.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository. Cannot install hook.")

    hooks_dir = os.path.join(git_root, ".git", "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    post_merge_path = os.path.join(hooks_dir, "post-merge")

    hook_script_content = _generate_post_merge_hook_content()

    try:
        if os.path.exists(post_merge_path):
            if non_interactive:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A post-merge hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield doctor' calls to your existing hook script."
                )
                return

            if not force:
                overwrite = questionary.confirm(
                    "A post-merge hook already exists. Do you want to overwrite it?",
                    default=False,
                ).ask()
                if not overwrite:
                    console.print("[yellow]Hook installation cancelled.[/yellow]")
                    raise typer.Exit()

        with open(post_merge_path, "w") as f:
            f.write(hook_script_content)

        current_permissions = os.stat(post_merge_path).st_mode
        os.chmod(
            post_merge_path,
            current_permissions | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
        )

        console.print(
            "[bold green]✓ Git post-merge hook installed successfully![/bold green]"
        )

    except (IOError, OSError) as e:
        raise EnvShieldException(
            f"Failed to write or set permissions for the hook file: {e}"
        )
    except (TypeError, KeyboardInterrupt):
        console.print("[yellow]Hook installation cancelled by user.[/yellow]")
        raise typer.Exit()
