# envshield/core/scanner.py
import difflib
import fnmatch
import os
import re
import shlex
import stat
from typing import Any, Dict, List, Optional

import questionary
import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config import manager as config_manager
from ..core.exceptions import EnvShieldException, SchemaNotFoundError
from ..utils import git_utils
from . import discovery

console = Console()

SECRET_PATTERNS: List[Dict[str, str]] = [
    {
        # Value may be quoted (Python/JSON-style: KEY = "value") or bare
        # (dotenv-style: KEY=value) -- real .env files are conventionally
        # unquoted, so requiring quotes here used to make this pattern blind
        # to the exact file format EnvShield exists to protect. The unquoted
        # branch is bounded on both sides so it can't start/stop mid-token.
        "name": "Generic API Key",
        # No leading lookbehind on the unquoted branch: the value's start is
        # already unambiguously anchored by the preceding literal '[:=]\s*'
        # (unlike the AWS pattern below, which has no such anchor). Adding
        # one here would misfire on the single most common shape -- 'KEY='
        # with no space -- since '=' is itself a member of the value charset.
        "pattern": r"(?i)(key|api(?!version)|token|secret|password|auth|credential)[a-z0-9_ .\-,]{0,25}\s*[:=]\s*(?:['\"][0-9a-zA-Z\-_=]{16,64}['\"]|[0-9a-zA-Z\-_=]{16,64}(?![0-9a-zA-Z\-_=]))",
    },
    {
        # Matches either the header or footer line, in case one was
        # deliberately stripped from a leaked key blob.
        "name": "Private Key",
        "pattern": r"-----(?:BEGIN|END) (?:EC|PGP|DSA|RSA|OPENSSH|ENCRYPTED)? ?PRIVATE KEY(?: BLOCK)?-----",
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
        # Boundary checks deliberately exclude '=' (unlike the value charset
        # itself, which includes it for base64 padding): AWS secrets are
        # typically written straight after a bare '=' with no space, and '='
        # is also a legal trailing content char, so treating it as "still
        # part of a token" here would reject the exact 'KEY=<secret>' shape
        # this branch exists to catch.
        "pattern": r"(?i)aws(.{0,20})?(?:['\"][0-9a-zA-Z\/+=]{40}['\"]|(?<![0-9a-zA-Z\/+])[0-9a-zA-Z\/+=]{40}(?![0-9a-zA-Z\/+]))",
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


# O_NOFOLLOW doesn't exist in the os module at all on Windows (creating a
# real filesystem symlink there also requires a privilege an ordinary user
# doesn't have by default, unlike POSIX) -- checked once at import time
# rather than via a try/except on every call.
_CAN_USE_O_NOFOLLOW = hasattr(os, "O_NOFOLLOW")


def _open_for_scan(file_path: str):
    """
    Opens a file for scanning -- the actual security boundary against a
    symlink that appears after _collect_files_to_scan's own islink() checks
    already ran and let the path through (a real, if narrow, TOCTOU
    window: those checks and this open are separate syscalls, with every
    other file ahead of this one in the scan list executing in between).

    On a platform with O_NOFOLLOW, the kernel refuses to open the path if
    its *final* path component is a symlink, as part of this single
    open() syscall -- there's no separate check to race against, because
    the check and the read happen atomically together. If the path became
    a symlink after collection, this raises OSError (ELOOP), which the
    caller already handles the same way as any other unreadable file --
    the scan skips that one file and continues, it doesn't crash.

    On a platform without O_NOFOLLOW (Windows), this falls back to a plain
    open with no symlink protection -- an accepted, documented gap there
    rather than a silent one, and one the base attack surface is already
    narrower against by default (see module-level note above). Deliberately
    out of scope: a symlinked *ancestor directory* swapped mid-walk (would
    need dir_fd-relative opens component by component) and the unrelated
    file-size TOCTOU on the >1MB skip check -- both tracked separately, not
    fixed here.
    """
    flags = os.O_RDONLY
    if _CAN_USE_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    fd = os.open(file_path, flags)
    return os.fdopen(fd, "r", encoding="utf-8", errors="ignore")


def _redact_match(matched_text: str) -> str:
    """
    Turns a matched secret span into a safe, non-reversible preview: the
    character count only, never any part of the value itself.

    No boundary characters are shown, at any length. For a short secret (a
    placeholder, a short token) even one or two boundary characters can be
    a large fraction of the whole value, so there's no length threshold
    above which partial disclosure becomes safe enough to bother with --
    length alone is still enough to tell a real-looking key apart from an
    empty or placeholder value.
    """
    return f"<redacted, {len(matched_text)} chars>"


def _get_diff_lines(file_path: str) -> Optional[set]:
    """Get line numbers that are newly added in the staged version.

    Compares the staged version against HEAD to find lines that are new in
    this commit (present in staged but not in HEAD).

    Args:
        file_path: Path to the file to check

    Returns:
        set: Line numbers (1-indexed) that are newly added
        None: If the file doesn't exist in HEAD (brand new file) - scan all lines
        empty set: If there are no new lines
    """
    try:
        head_content = git_utils.get_head_file_content(file_path)

        # Brand new file - return None to indicate "scan all lines"
        if head_content is None:
            return None

        staged_content = git_utils.get_staged_file_content(file_path)
        if staged_content is None:
            return set()

        # Extract lines
        head_lines = head_content.splitlines()
        staged_lines = staged_content.splitlines()

        # A positional diff, not a content-set comparison: matching by exact
        # line text alone would treat a genuinely new line as "pre-existing"
        # whenever some unrelated line elsewhere in the file happens to have
        # identical text (e.g. a repeated comment or template block) --
        # letting a real new secret hide behind a coincidental text match.
        matcher = difflib.SequenceMatcher(
            None, head_lines, staged_lines, autojunk=False
        )
        new_line_numbers = set()
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace"):
                new_line_numbers.update(range(j1 + 1, j2 + 1))

        return new_line_numbers
    except Exception:
        # On any error, return empty set (conservative - don't scan)
        return set()


def _record_discovered_usages(
    undeclared_findings: List[Dict],
    usages,
    schema_vars: set,
    new_lines_only: Optional[set],
) -> None:
    """Shared adapter for both discovery.py engines: filters by
    new_lines_only and schema_vars, then appends in the same finding shape
    the (now-removed) per-line regex loop always used -- callers of
    _scan_single_file see no difference."""
    for usage in usages:
        if new_lines_only is not None and usage.line not in new_lines_only:
            continue
        if usage.variable not in schema_vars:
            undeclared_findings.append(
                {
                    "file_path": usage.file_path,
                    "line_num": usage.line,
                    "variable_name": usage.variable,
                }
            )


def _scan_single_file(
    file_path: str,
    schema_vars: set,
    content: Optional[str] = None,
    new_lines_only: Optional[set] = None,
) -> (List[Dict], List[Dict]):
    """
    Helper to scan one file for both secrets and undeclared variables.
    Returns two lists: one for secrets, one for undeclared variables.

    If `content` is provided, it's scanned directly instead of reading the
    file from disk — used for `--staged` scans, where we must scan what's
    actually staged in the Git index, not the working-tree copy (which can
    differ, e.g. if a secret was staged and then edited out without
    re-staging).

    If `new_lines_only` is provided, only those line numbers are scanned.
    Used for diff-aware scanning of excluded files.
    """
    secret_findings = []
    undeclared_findings = []

    try:
        if content is not None:
            lines = content.splitlines(keepends=True)
        else:
            with _open_for_scan(file_path) as f:
                lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            # If new_lines_only is specified, skip lines not in that set
            if new_lines_only is not None and line_num not in new_lines_only:
                continue

            # Check for secrets
            for secret in SECRET_PATTERNS:
                match = re.search(secret["pattern"], line)
                if match:
                    secret_findings.append(
                        {
                            "file_path": file_path,
                            "line_num": line_num,
                            "secret_type": secret["name"],
                            # Only the matched span's length, never the raw
                            # line or any part of the matched value itself --
                            # see _redact_match.
                            "redacted_preview": _redact_match(match.group(0)),
                        }
                    )
                    break

        # Undeclared-variable detection for both Python and JS/TS runs once
        # over the whole file (not per line, like the secret loop above),
        # since a usage can span multiple lines -- see discovery.py.
        # Skipped entirely when new_lines_only is an explicitly empty set:
        # nothing could survive that filter anyway.
        if new_lines_only != set():
            full_text = "".join(lines)
            if file_path.endswith(".py"):
                _record_discovered_usages(
                    undeclared_findings,
                    discovery.discover_python_usages(full_text, file_path),
                    schema_vars,
                    new_lines_only,
                )
            elif file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
                _record_discovered_usages(
                    undeclared_findings,
                    discovery.discover_js_usages(full_text, file_path),
                    schema_vars,
                    new_lines_only,
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

    # A typo'd path must fail loudly, not silently scan zero files and
    # report "no issues found" -- that reads as "your code is clean" when
    # what actually happened is "nothing was scanned at all."
    missing_paths = [p for p in scan_paths if not os.path.exists(p)]
    if missing_paths:
        raise EnvShieldException(
            f"Path not found: {', '.join(missing_paths)}. Check for a typo."
        )

    if "." in scan_paths:
        console.print("Scanning [yellow]current directory[/yellow] recursively...")

    # A symlink can point anywhere on disk -- reading through one would let
    # a file outside the directory the caller actually asked to scan be
    # read (and its findings reported) as if it were part of the scan.
    # os.walk's own symlink protection (followlinks=False, the default)
    # only stops it recursing into a symlinked subdirectory *discovered
    # during* the walk -- it does nothing for a symlinked file leaf, and
    # nothing at all for the top-level path argument itself if that's a
    # symlink to a directory. Both are checked explicitly below so no
    # symlink -- however it's reached -- is ever opened.
    skipped_symlinks = []

    for path in scan_paths:
        if os.path.islink(path):
            skipped_symlinks.append(path)
            continue
        if os.path.isfile(path):
            files_to_scan.append(os.path.abspath(path))
        elif os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                # Prune in-place so os.walk doesn't descend into these dirs at all.
                dirs[:] = [d for d in dirs if not _is_default_excluded_dir(d)]
                for file in files:
                    file_path = os.path.join(root, file)
                    if os.path.islink(file_path):
                        skipped_symlinks.append(file_path)
                        continue
                    files_to_scan.append(file_path)

    if skipped_symlinks:
        console.print(
            f"[dim]ℹ️  Skipping {len(skipped_symlinks)} symlink(s) -- not "
            "followed, to avoid reading a file outside the scanned "
            f"directory: {', '.join(sorted(skipped_symlinks))}[/dim]"
        )

    # A git-ignored file (a real '.env', chief among them) is never going
    # to be committed, so flagging a secret inside it as "DANGER" is pure
    # noise against what this command actually exists to prevent -- and
    # actively contradicts its own suggestion text, which tells you to
    # move secrets INTO that same gitignored file. '--staged' is
    # unaffected: if an ignored file somehow got staged anyway, that's a
    # real, imminent risk worth flagging, not noise.
    ignored = git_utils.get_ignored_files(files_to_scan)
    if ignored:
        console.print(
            f"[dim]ℹ️  Skipping {len(ignored)} git-ignored file(s) -- not "
            "committable, so not scanned: "
            f"{', '.join(sorted(ignored))}[/dim]"
        )
        files_to_scan = [f for f in files_to_scan if f not in ignored]

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

    An explicit `service_name` checks every file against that one schema --
    unchanged, single-target behavior.

    Otherwise, each configured service's own schema is matched against
    files under that service's own directory (the directory its schema
    lives in) -- a file under 'alpha/' is checked against alpha's schema,
    not beta's. A single-service project's one (and only) service has a
    directory of '.' (its schema lives at the project root), which matches
    every file -- there's no separate "root schema" concept to fall back to
    anymore: envshield.yml always has at least one registered service (see
    generate_default_config_content), single-service or not, so every
    schema is a service's schema. Without the directory matching below,
    running the pre-commit hook's plain `envshield scan --staged` (no
    --service) on a multi-service project would have no way to tell which
    service's schema applies to which file.
    """
    if service_name:
        try:
            schema_vars = set(
                config_manager.load_schema(service_name=service_name).keys()
            )
            console.print("[dim]Schema loaded for compliance check.[/dim]")
        except SchemaNotFoundError:
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
        except SchemaNotFoundError:
            continue
        except EnvShieldException as e:
            # A broken schema in one service (e.g. mid-edit, unrelated to
            # what's actually staged) must not block undeclared-variable
            # checking -- or the commit itself, via 'scan --staged' -- for
            # every other service in the project. Real incident this
            # reproduces: a malformed env.schema.toml sitting in service
            # A's working tree silently blocked every commit touching
            # service B, even though B was never involved.
            console.print(
                f"[yellow]Warning: Could not load schema for service '{name}': {e} "
                f"Skipping its undeclared-variable check.[/yellow]"
            )
            continue
        service_dirs.append((service_dir, schema_vars))
    # Longest directory first, so a nested service dir wins over a shorter
    # sibling -- and so a single-service project's '.' entry only ever acts
    # as the last-resort catch-all it should be, not a premature match.
    service_dirs.sort(key=lambda item: len(item[0]), reverse=True)

    if not service_dirs:
        console.print(
            "[yellow]Warning: No services configured. Skipping undeclared variable check.[/yellow]"
        )
        return None

    console.print("[dim]Per-service schemas loaded for compliance check.[/dim]")

    def _resolve(file_path: str) -> set:
        normalized = _normalize_for_dir_match(file_path)
        for service_dir, schema_vars in service_dirs:
            if (
                service_dir == "."
                or normalized == service_dir
                or normalized.startswith(service_dir + os.sep)
            ):
                return schema_vars
        return set()

    return _resolve


def _scan_files(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
):
    """
    Does the actual file collection and scanning, returning the raw
    (secret_findings, undeclared_findings, skipped_large_files) lists.

    Extracted from run_scan so both the Rich-rendering path and the
    '--json' path (see scan_result) share one implementation instead of
    two copies that could quietly drift apart.

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

    # For staged scans: keep excluded files for diff-aware scanning
    # For non-staged scans: filter out excluded files as before
    if staged_only:
        final_files_to_scan = files_to_scan
        excluded_files = set()
        for pattern in all_exclusions:
            for file_path in files_to_scan:
                normalized_path = file_path.replace(os.getcwd() + os.sep, "")
                if fnmatch.fnmatch(normalized_path, pattern):
                    excluded_files.add(file_path)
    else:
        final_files_to_scan = _filter_files(files_to_scan, all_exclusions)
        excluded_files = set()

    all_secret_findings = []
    all_undeclared_findings = []
    skipped_large_files = []

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
                if content is None:
                    continue
                if len(content) > 1_000_000:
                    skipped_large_files.append(file_path)
                    continue

                # Diff-aware scanning for excluded files
                new_lines_only = None
                if file_path in excluded_files:
                    new_lines = _get_diff_lines(file_path)
                    if new_lines is None:
                        # Brand new file - scan all lines despite exclusion
                        console.print(
                            f"[yellow]ℹ️  Scanning new file {os.path.basename(file_path)} (despite exclusion)[/yellow]"
                        )
                        new_lines_only = None
                    elif len(new_lines) == 0:
                        # File is excluded and has no new lines - skip it
                        continue
                    else:
                        # File is excluded, but scan only newly-added lines
                        console.print(
                            f"[dim]ℹ️  {os.path.basename(file_path)} (excluded; diffs only: {len(new_lines)} new line(s))[/dim]"
                        )
                        new_lines_only = new_lines

                secrets, undeclared = _scan_single_file(
                    file_path,
                    schema_vars,
                    content=content,
                    new_lines_only=new_lines_only,
                )
            else:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 1_000_000:
                    skipped_large_files.append(file_path)
                    continue
                secrets, undeclared = _scan_single_file(file_path, schema_vars)

            all_secret_findings.extend(secrets)
            all_undeclared_findings.extend(undeclared)

    return all_secret_findings, all_undeclared_findings, skipped_large_files


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
    all_secret_findings, all_undeclared_findings, skipped_large_files = _scan_files(
        paths, staged_only, config_path, exclude_patterns, service_name
    )

    if skipped_large_files:
        console.print(
            f"\n[bold yellow]⚠️  Skipped {len(skipped_large_files)} file(s) over 1MB (not scanned -- coverage is incomplete for these):[/bold yellow]"
        )
        for skipped_path in skipped_large_files:
            console.print(f"    [dim]{skipped_path}[/dim]")

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
        table.add_column("Preview", style="white")
        for finding in all_secret_findings:
            table.add_row(
                finding["file_path"],
                str(finding["line_num"]),
                finding["secret_type"],
                finding["redacted_preview"],
            )
        console.print(table)
        console.print(
            "\n[bold]Suggestion:[/bold] Remove the secret from this file and move it "
            "to '.env' (gitignored) instead. If it's a known false positive, add a "
            "targeted '--exclude' glob or a 'secret_scanning.exclude_files' entry in envshield.yml."
        )

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


def scan_result(
    paths: Optional[List[str]],
    staged_only: bool,
    config_path: Optional[str],
    exclude_patterns: Optional[List[str]],
    service_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Same scan as run_scan, but silences every Rich print/progress-bar (so
    stdout stays pure JSON) and returns a plain, JSON-serializable dict
    instead of rendering tables and raising typer.Exit -- for '--json'.
    """
    was_quiet = console.quiet
    console.quiet = True
    try:
        secrets, undeclared, skipped = _scan_files(
            paths, staged_only, config_path, exclude_patterns, service_name
        )
    finally:
        console.quiet = was_quiet

    return {
        "clean": not (secrets or undeclared),
        "secrets": secrets,
        "undeclared_variables": undeclared,
        "skipped_files": skipped,
    }


ENVSHIELD_HOOK_MARKER = "# Hook installed by EnvShield"


def remove_hooks() -> List[str]:
    """
    Removes any EnvShield-installed Git hook (pre-commit, post-merge),
    identified by the same '# Hook installed by EnvShield' marker `install`
    already checks before offering to overwrite -- a hook that isn't
    EnvShield's is left alone rather than deleted out from under whatever
    else manages it (Husky, a hand-written script, etc.).

    Returns the names of the hooks actually removed.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository.")

    hooks_dir = git_utils.get_hooks_dir()
    removed = []
    for hook_name in ("pre-commit", "post-merge"):
        hook_path = os.path.join(hooks_dir, hook_name)
        if not os.path.exists(hook_path):
            continue
        with open(hook_path, "r") as f:
            content = f.read()
        if ENVSHIELD_HOOK_MARKER not in content:
            continue
        os.remove(hook_path)
        removed.append(hook_name)
    return removed


def _describe_existing_hook(content: str) -> str:
    """Best-effort description of an existing hook file, for the overwrite warning."""
    if ENVSHIELD_HOOK_MARKER in content:
        return "previously installed by EnvShield -- safe to regenerate"
    if "husky.sh" in content or ".husky" in content:
        return "managed by Husky"
    non_comment_lines = [
        line
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return f"NOT installed by EnvShield -- overwriting will delete {len(non_comment_lines)} existing line(s) of hook logic"


def _warn_hooks_path_redirect(hooks_dir: str, git_root: str) -> None:
    default_dir = os.path.join(git_root, ".git", "hooks")
    if os.path.abspath(hooks_dir) != os.path.abspath(default_dir):
        console.print(
            f"[dim]ℹ️  core.hooksPath is set -- installing into {hooks_dir} instead of .git/hooks.[/dim]"
        )


def _generate_pre_commit_hook_content() -> str:
    """
    Generates the bash script content for the pre-commit hook: always scans
    staged files for secrets/undeclared vars, and -- when a staged change
    touches a service's schema -- also blocks the commit if that service's
    tracked template (.env.example) wasn't regenerated to match. Without
    this, a hand-edited env.schema.toml can go stale at the source, silent
    until whoever next pulls (and only then if they have the post-merge
    hook installed at all).
    """
    config = config_manager.load_config()
    services_config = config.get("services", {})

    schema_to_service = {
        service_config["schema"]: name
        for name, service_config in services_config.items()
        if isinstance(service_config, dict) and service_config.get("schema")
    }

    header = (
        "#!/bin/sh\n\n"
        "# Hook installed by EnvShield\n"
        "# Scans staged files for hardcoded secrets AND undeclared environment variables.\n"
        "envshield scan --staged\n"
        "STATUS=$?\n"
    )

    if not schema_to_service:
        return header + "exit $STATUS\n"

    # Each service's template sync is only checked when THAT service's own
    # schema was actually staged -- one 'if' per service, gated on its own
    # path with grep -F (a literal-string match, no alternation needed).
    # A single shared 'if' gating every service's check (the previous
    # shape) is the same class of bug the combined -E alternation was
    # meant to fix elsewhere: staging only 'web's schema still ran 'api's
    # check too, false-failing the commit over a service nobody touched.
    #
    # schema_path/name/example_file all come from envshield.yml -- a file
    # explicitly designed to be committed and PR-edited (see
    # config_manager.UnsafePathError's own docstring on this exact threat
    # model). Each is assigned to a shell variable via shlex.quote() -- a
    # single, self-contained safe shell word -- and every use after that
    # references it as "$VAR", never re-embeds the raw value as literal
    # script text. Double-quoted parameter expansion substitutes a
    # variable's value as opaque data with no further shell
    # interpretation of its content, regardless of what characters it
    # contains -- that's what actually closes the injection here, not
    # anything about the value's own contents being "safe-looking".
    blocks = []
    for schema_path, name in schema_to_service.items():
        lines = [
            f"_ENVSHIELD_SCHEMA_PATH={shlex.quote(schema_path)}",
            f"_ENVSHIELD_SERVICE_NAME={shlex.quote(name)}",
            'if git diff --cached --name-only | grep -qF "$_ENVSHIELD_SCHEMA_PATH"; then',
        ]

        # 'schema sync --check' below only ever reads the template off
        # disk, not what's actually staged -- so running 'schema sync'
        # (which updates disk) and then forgetting to 'git add' the
        # result would pass that check while the commit itself still
        # lands with a stale template baked in. Real incident this
        # reproduces. Blocking on ANY unstaged template diff here closes
        # that gap without needing to duplicate schema/template parsing
        # against git's staged blob content. Python-module services have
        # no separate template file to check (see _check_example_file_sync).
        try:
            paths = config_manager.get_env_paths(service_name=name)
            if not paths["local_file"].endswith(".py"):
                example_file = paths["example_file"]
                lines.append(f"  _ENVSHIELD_EXAMPLE_FILE={shlex.quote(example_file)}")
                lines.append(
                    '  if git diff --name-only | grep -qF "$_ENVSHIELD_EXAMPLE_FILE"; then\n'
                    "    echo \"✗ '$_ENVSHIELD_EXAMPLE_FILE' has unstaged changes -- did you "
                    "forget 'git add' after running 'envshield schema sync'?\"\n"
                    "    STATUS=1\n"
                    "  fi"
                )
        except EnvShieldException:
            pass

        lines.append(
            '  envshield schema sync --service "$_ENVSHIELD_SERVICE_NAME" --check || STATUS=1'
        )
        lines.append("fi")
        blocks.append("\n".join(lines))

    return (
        header
        + "\n# A staged schema change must also update its tracked template --\n"
        + "# otherwise .env.example goes stale the moment this commit lands.\n"
        + "\n".join(blocks)
        + "\n\n"
        + "exit $STATUS\n"
    )


def install_pre_commit_hook(force: bool = False, non_interactive: bool = False):
    """Installs the Git pre-commit hook."""
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository. Cannot install hook.")

    hooks_dir = git_utils.get_hooks_dir()
    _warn_hooks_path_redirect(hooks_dir, git_root)
    os.makedirs(hooks_dir, exist_ok=True)
    pre_commit_path = os.path.join(hooks_dir, "pre-commit")

    hook_script_content = _generate_pre_commit_hook_content()

    try:
        if os.path.exists(pre_commit_path):
            with open(pre_commit_path, "r") as f:
                existing_content = f.read()

            # Regenerating EnvShield's own hook is always safe -- non-interactive
            # mode only needs to hold back from a genuinely foreign one, not
            # bail out on every re-run just because the file already exists.
            if non_interactive and ENVSHIELD_HOOK_MARKER not in existing_content:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A pre-commit hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield scan --staged' to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A pre-commit hook already exists ({_describe_existing_hook(existing_content)}). Do you want to overwrite it?",
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
    Generates the bash script content for the post-merge hook. Smart: each
    service's 'doctor' check only runs when THAT service's own schema
    actually changed in the merge -- not every registered service
    whenever any one schema changed. Same class of false-positive-across-
    services bug the pre-commit hook had: merging a branch that only
    touched 'api's schema must not also run (and potentially report
    issues for) 'web', which nothing in this merge affected at all.
    """
    config = config_manager.load_config()
    services_config = config.get("services", {})

    schema_to_service = {
        service_config["schema"]: name
        for name, service_config in services_config.items()
        if isinstance(service_config, dict) and service_config.get("schema")
    }

    if not schema_to_service:
        # Hooks can be installed before any service is registered (e.g. a
        # standalone 'hook install' in a fresh repo) -- nothing to watch
        # for yet, so there's nothing this hook can usefully check.
        return (
            "#!/bin/sh\n\n"
            "# Hook installed by EnvShield\n"
            "# No services registered yet -- nothing to check.\n"
            "exit 0\n"
        )

    # One 'if' per service, gated on grep -F matching that service's own
    # schema path only (a literal-string match -- no alternation needed,
    # unlike the old shared gate this replaces).
    #
    # schema_path/name come from envshield.yml, untrusted for the same
    # reason noted in _generate_pre_commit_hook_content -- same fix here:
    # shlex.quote() into a shell variable, referenced afterward only as
    # "$VAR", never re-embedded as literal script text.
    checks = "\n".join(
        f"_ENVSHIELD_SCHEMA_PATH={shlex.quote(schema_path)}\n"
        f"_ENVSHIELD_SERVICE_NAME={shlex.quote(name)}\n"
        'if git diff --name-only HEAD@{1}..HEAD | grep -qF "$_ENVSHIELD_SCHEMA_PATH" 2>/dev/null; then\n'
        '  envshield doctor --service "$_ENVSHIELD_SERVICE_NAME" 2>/dev/null\n'
        "fi"
        for schema_path, name in schema_to_service.items()
    )
    return (
        "#!/bin/sh\n\n"
        "# Hook installed by EnvShield\n"
        "# Smart: only runs for a service whose own schema actually changed in this merge.\n"
        "# If new required variables were added, it alerts the developer immediately.\n"
        "# Non-blocking: warns but doesn't fail the merge.\n\n"
        + checks
        + "\n\n"
        + "exit 0\n"
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

    hooks_dir = git_utils.get_hooks_dir()
    _warn_hooks_path_redirect(hooks_dir, git_root)
    os.makedirs(hooks_dir, exist_ok=True)
    post_merge_path = os.path.join(hooks_dir, "post-merge")

    hook_script_content = _generate_post_merge_hook_content()

    try:
        if os.path.exists(post_merge_path):
            with open(post_merge_path, "r") as f:
                existing_content = f.read()

            # Regenerating EnvShield's own hook is always safe -- non-interactive
            # mode only needs to hold back from a genuinely foreign one, not
            # bail out on every re-run just because the file already exists.
            if non_interactive and ENVSHIELD_HOOK_MARKER not in existing_content:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A post-merge hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield doctor' calls to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A post-merge hook already exists ({_describe_existing_hook(existing_content)}). Do you want to overwrite it?",
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
