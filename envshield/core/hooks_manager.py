"""Manages Git hooks installation and lifecycle."""

import os
import stat
import subprocess
import sys
from typing import Dict, Iterable, List, Tuple

import questionary
import typer
from rich.console import Console

from ..config import manager as config_manager
from ..core.exceptions import EnvShieldException, ServiceConfigError
from ..utils import git_utils

console = Console()


ENVSHIELD_HOOK_MARKER = "# Hook installed by EnvShield"


def remove_hooks() -> List[Dict[str, str]]:
    """
    Removes only a hook file EnvShield can prove is its own, unmodified
    output -- an exact content match against what EnvShield would generate
    right now, the same ownership standard install_pre_commit_hook/
    install_post_merge_hook already use (see their own comment: the marker
    comment alone is not proof of ownership -- a user can modify a
    generated hook, or hand-write one containing the same comment, while
    keeping the marker text intact). A hook that isn't provably
    EnvShield's own unmodified output is left alone -- whether it's
    genuinely foreign (Husky, a hand-written script) or an EnvShield hook
    a user has since extended.

    Returns one {"name": ..., "status": ...} entry per hook type EnvShield
    manages (pre-commit, post-merge), regardless of outcome -- "removed"
    (deleted, exact match), "preserved" (a file exists but its content
    doesn't exactly match current generated output -- hand-modified,
    foreign, or stale relative to the current config), or "missing" (no
    file at that path). This is the single source of truth for hook
    ownership; 'hook remove' filters for "removed", 'uninstall' also
    reports "preserved" -- neither re-derives the ownership check itself.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository.")

    hooks_dir = git_utils.get_hooks_dir()
    generators = {
        "pre-commit": _generate_pre_commit_hook_content,
        "post-merge": _generate_post_merge_hook_content,
    }
    results = []
    for hook_name, generate_content in generators.items():
        hook_path = os.path.join(hooks_dir, hook_name)
        if not os.path.exists(hook_path):
            results.append({"name": hook_name, "status": "missing"})
            continue
        with open(hook_path, "r") as f:
            content = f.read()
        if content != generate_content():
            results.append({"name": hook_name, "status": "preserved"})
            continue
        os.remove(hook_path)
        results.append({"name": hook_name, "status": "removed"})
    return results


def _describe_existing_hook(content: str, is_safely_regeneratable: bool) -> str:
    """
    Best-effort description of an existing hook file, for the overwrite
    warning. `is_safely_regeneratable` is an exact-content match against
    what EnvShield would generate right now -- the marker comment alone is
    not proof of that (see the call site's own comment): a user can modify
    a generated hook, or hand-write one, while keeping the marker text
    intact.
    """
    if is_safely_regeneratable:
        return "previously installed by EnvShield -- safe to regenerate"
    if ENVSHIELD_HOOK_MARKER in content:
        return (
            "previously installed by EnvShield, but its contents no longer match "
            "what EnvShield would generate now (hand-edited, or stale relative to "
            "your current config) -- overwriting could discard those changes"
        )
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


# The installed hooks are constant shims: identical bytes for every
# project and every envshield.yml. What they cover (which schemas, which
# services) is resolved each time they run, by 'envshield hook run' (see
# hooks_manager.run_pre_commit/run_post_merge) -- so adding a service or
# sharing a schema never leaves an installed hook stale, remove/uninstall's
# exact-match ownership check keeps working after any config change, and no
# value from envshield.yml (a committed, PR-editable file) is ever
# interpolated into a shell script.
_PRE_COMMIT_HOOK = (
    "#!/bin/sh\n\n"
    f"{ENVSHIELD_HOOK_MARKER}\n"
    "# Scans staged files for secrets and undeclared variables, and checks the\n"
    "# template of every service whose schema (or a schema it extends) is staged.\n"
    "# What that covers is read from envshield.yml each time this runs.\n"
    "exec envshield hook run pre-commit\n"
)

_POST_MERGE_HOOK = (
    "#!/bin/sh\n\n"
    f"{ENVSHIELD_HOOK_MARKER}\n"
    "# Runs 'envshield doctor' for every service whose schema (or a schema it\n"
    "# extends) changed in this merge. Non-blocking: warns, never fails the merge.\n"
    "exec envshield hook run post-merge\n"
)


def _generate_pre_commit_hook_content() -> str:
    return _PRE_COMMIT_HOOK


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

            # A marker comment alone is not proof this file is EnvShield's own,
            # untouched output -- a user can modify a generated hook (or
            # hand-write one containing the same comment) while keeping the
            # marker text intact. Only an exact match against what EnvShield
            # would generate right now is provably safe to replace without
            # asking; non-interactive mode's fast path is scoped to exactly
            # that case -- anything else (including a marker-bearing file
            # that no longer matches) falls through to the same protected
            # confirmation/warning path as a genuinely foreign hook.
            is_safely_regeneratable = existing_content == hook_script_content

            if non_interactive and not is_safely_regeneratable:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A pre-commit hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield hook run pre-commit' to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A pre-commit hook already exists ({_describe_existing_hook(existing_content, is_safely_regeneratable)}). Do you want to overwrite it?",
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
    return _POST_MERGE_HOOK


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

            # See install_pre_commit_hook's identical comment: a marker
            # comment alone is not proof this file is EnvShield's own,
            # untouched output. Only an exact match against what EnvShield
            # would generate right now is treated as provably safe.
            is_safely_regeneratable = existing_content == hook_script_content

            if non_interactive and not is_safely_regeneratable:
                console.print(
                    "[bold yellow]⚠️  Warning:[/] A post-merge hook already exists. EnvShield was not installed automatically."
                )
                console.print(
                    "    Please add 'envshield hook run post-merge' to your existing hook script."
                )
                return

            if not force and not non_interactive:
                overwrite = questionary.confirm(
                    f"A post-merge hook already exists ({_describe_existing_hook(existing_content, is_safely_regeneratable)}). Do you want to overwrite it?",
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


# The opening lines of the per-service hooks EnvShield generated before
# the hook became a constant shim. Only used to *describe* such a file in
# 'hook status' -- never as proof of ownership: install/remove still
# require an exact match against the current shim (BL-119/BL-120).
_LEGACY_HOOK_PREFIXES = {
    "pre-commit": (
        "#!/bin/sh\n\n# Hook installed by EnvShield\n"
        "# Scans staged files for hardcoded secrets AND undeclared environment variables.\n",
    ),
    "post-merge": (
        "#!/bin/sh\n\n# Hook installed by EnvShield\n"
        "# Smart: only runs for a service whose own schema actually changed in this merge.\n",
        "#!/bin/sh\n\n# Hook installed by EnvShield\n"
        "# No services registered yet -- nothing to check.\n",
    ),
}


def schema_coverage() -> List[Tuple[str, List[str], List[str]]]:
    """
    What the hooks protect, resolved from the current envshield.yml: one
    (schema_path, files, users) entry per registered schema file, in
    envshield.yml order. `files` is the schema plus its `extends` chain
    (config_manager.get_schema_files, real paths); `users` is every
    service using that schema (config_manager.get_schema_users).

    A schema that doesn't exist yet covers just its own path -- creating
    it is then a change to that path like any other. Fails closed: a
    service whose schema can't be determined raises instead of being left
    out of coverage.
    """
    coverage = []
    seen = set()
    for name in config_manager.get_services():
        schema = config_manager.get_service_schema_path(name)
        if not schema:
            raise ServiceConfigError(
                f"Service '{name}' in {config_manager.CONFIG_FILE_NAME} has no "
                "'schema:' path, so EnvShield can't tell which changes affect it."
            )
        key = os.path.realpath(schema)
        if key in seen:
            continue
        seen.add(key)
        files = (
            config_manager.get_schema_files(schema) if os.path.exists(schema) else [key]
        )
        coverage.append((schema, files, config_manager.get_schema_users(schema)))
    return coverage


def affected_services(changed_paths: Iterable[str]) -> List[str]:
    """
    Registered services a change to `changed_paths` (absolute, or relative
    to the project root) can affect, in envshield.yml order: every user of
    a schema whose file set (the schema or anything it extends) changed,
    or every service when envshield.yml itself changed. Chooses which
    services to check -- never what any of them is granted.
    """
    changed = {os.path.realpath(path) for path in changed_paths}
    services = list(config_manager.get_services())
    # Resolved even when envshield.yml changed, so a broken topology fails
    # the same way whichever file triggered it.
    coverage = schema_coverage()
    if os.path.realpath(config_manager.CONFIG_FILE_NAME) in changed:
        return services
    hit = {
        user
        for _schema, files, users in coverage
        if changed.intersection(files)
        for user in users
    }
    return [name for name in services if name in hit]


def _changed_files(*diff_args: str) -> List[str]:
    """
    Absolute paths 'git diff --name-only' reports for `diff_args`. Unlike
    git_utils.list_changed_files this raises on failure: a hook that
    can't tell what changed must not conclude that nothing did. '-z'
    keeps unusual file names unquoted; '--no-renames' reports a rename as
    both of its paths.
    """
    git_root = git_utils.get_git_root()
    if not git_root:
        raise EnvShieldException("Not inside a Git repository.")
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", *diff_args],
        cwd=git_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EnvShieldException(
            f"'git diff {' '.join(diff_args)}' failed: {result.stderr.strip()}"
        )
    return [os.path.join(git_root, p) for p in result.stdout.split("\0") if p]


def _envshield(*args: str, **kwargs) -> int:
    """Runs an existing EnvShield command, from this same installation."""
    return subprocess.run(
        [sys.executable, "-m", "envshield", *args], **kwargs
    ).returncode


def run_pre_commit() -> int:
    """
    The pre-commit hook: scans staged files, then, for every service a
    staged change affects, checks its tracked template is both staged and
    in sync with its schema. Returns the hook's exit code; anything
    non-zero blocks the commit, including not being able to work out
    which services are affected.
    """
    status = _envshield("scan", "--staged", "--enforce")
    try:
        services = affected_services(_changed_files("--cached"))
        unstaged = {os.path.realpath(p) for p in _changed_files()}
    except EnvShieldException as e:
        console.print(
            "[bold red]Error:[/bold red] EnvShield couldn't work out which "
            f"services this commit affects: {e}"
        )
        return 1

    checked_templates = set()
    for name in services:
        try:
            paths = config_manager.get_env_paths(service_name=name)
        except EnvShieldException as e:
            console.print(f"[bold red]Error:[/bold red] {e}")
            status = 1
            continue
        # 'schema sync --check' reads the template off disk, not what's
        # staged -- syncing it and forgetting 'git add' would pass that
        # check while the commit still lands with the stale template. A
        # template shared by several services is one file: check it once.
        # A Python-module local_file has no separate template.
        example_file = paths["example_file"]
        template = os.path.realpath(example_file)
        if (
            not paths["local_file"].endswith(".py")
            and template not in checked_templates
        ):
            checked_templates.add(template)
            if template in unstaged:
                console.print(
                    f"✗ '{example_file}' has unstaged changes -- did you forget "
                    "'git add' after running 'envshield schema sync'?"
                )
                status = 1
        # Per service, not per template: each checks its own projection's
        # variables are in the file.
        if _envshield("schema", "sync", "--service", name, "--check") != 0:
            status = 1
    return status


def run_post_merge() -> int:
    """
    The post-merge hook: runs 'doctor' for every service the merge
    affected. Never blocks (always returns 0) -- it only warns.
    """
    try:
        services = affected_services(_changed_files("HEAD@{1}", "HEAD"))
    except EnvShieldException as e:
        console.print(
            "[yellow]⚠️  EnvShield couldn't check this merge's configuration "
            f"changes: {e}[/yellow]"
        )
        return 0
    for name in services:
        _envshield("doctor", "--service", name, stderr=subprocess.DEVNULL)
    return 0


def _is_interactive() -> bool:
    """
    Whether there's a real terminal to prompt on. Without this check,
    offering to install hooks from a non-interactive context (CI,
    'service discover --yes', a piped 'init') aborts with a raw questionary
    EOF error even after the actual work -- registering services, writing
    schemas -- already succeeded.
    """
    return sys.stdin.isatty()


class HooksManager:
    """Manages EnvShield git hooks (pre-commit, post-merge)."""

    def __init__(self):
        """Initialize the hooks manager."""
        try:
            self.git_root = git_utils.get_git_root()
        except Exception:
            self.git_root = None

    def are_hooks_installed(self) -> Tuple[bool, bool]:
        """Check if pre-commit and post-merge hooks are installed.

        Returns:
            Tuple[bool, bool]: (pre_commit_installed, post_merge_installed)
        """
        if not self.git_root:
            return False, False

        hooks_dir = git_utils.get_hooks_dir() or os.path.join(
            self.git_root, ".git", "hooks"
        )
        pre_commit_installed = os.path.exists(os.path.join(hooks_dir, "pre-commit"))
        post_merge_installed = os.path.exists(os.path.join(hooks_dir, "post-merge"))

        return pre_commit_installed, post_merge_installed

    def should_prompt_for_installation(self) -> bool:
        """Check if we should prompt the user to install hooks.

        Returns False if:
        - Not in a git repo
        - Hooks are already installed
        """
        if not self.git_root:
            return False

        pre_commit, post_merge = self.are_hooks_installed()
        return not (pre_commit and post_merge)

    def prompt_install_hooks(self) -> bool:
        """Ask the user if they want to install hooks.

        Returns:
            bool: True if user wants to install, False otherwise
        """
        if not self.should_prompt_for_installation():
            return False

        if not _is_interactive():
            return False

        pre_commit, post_merge = self.are_hooks_installed()

        if pre_commit and post_merge:
            # Both already installed
            return False

        missing = []
        if not pre_commit:
            missing.append("pre-commit (secret scanning)")
        if not post_merge:
            missing.append("post-merge (config change detection)")

        return questionary.confirm(
            f"Install git hooks? ({', '.join(missing)})",
            default=True,
            auto_enter=False,
        ).ask()

    def install_hooks_if_needed(self, auto: bool = False, force: bool = False) -> None:
        """Install hooks if they're missing.

        Args:
            auto: If True, ask user. If False, skip if not needed.
            force: If True, overwrite existing hooks.
        """
        if not self.git_root:
            if auto:
                console.print(
                    "[yellow]Not in a git repository. Cannot install hooks.[/yellow]"
                )
            return

        if not auto:
            # Only install if not already installed and user doesn't need prompting
            pre_commit, post_merge = self.are_hooks_installed()
            if pre_commit and post_merge:
                return
            # If both aren't installed, prompt anyway
            auto = True

        if auto and self.prompt_install_hooks():
            self._do_install_hooks(force=force)

    def _do_install_hooks(self, force: bool = False) -> None:
        """Actually install the hooks (called after user confirms).

        Only installs whichever hook(s) are actually missing -- an
        already-present hook must not be touched (and re-prompted for its
        own separate overwrite confirmation) merely because the OTHER hook
        needed installing. `prompt_install_hooks`'s own "Install git
        hooks? (...)" prompt already tells the user which hook(s) are
        about to be installed; installing an unrelated, already-present
        one here would silently do more than that prompt described.

        Args:
            force: If True, overwrite existing hooks.
        """
        try:
            pre_commit, post_merge = self.are_hooks_installed()
            if not pre_commit:
                install_pre_commit_hook(force=force, non_interactive=False)
            if not post_merge:
                install_post_merge_hook(force=force, non_interactive=False)
        except EnvShieldException as e:
            console.print(f"[bold yellow]⚠️  Warning:[/] {e}")

    def _hook_status_line(
        self, hooks_dir: str, filename: str, label: str, generator
    ) -> str:
        """
        Describes one hook file: missing, current (exactly EnvShield's
        shim), a legacy EnvShield hook, modified, or foreign. Purely
        informational -- install/remove decide ownership themselves, by
        exact match (BL-119/BL-120). The shim is the same for every
        configuration, so a change to envshield.yml never makes it stale.
        """
        path = os.path.join(hooks_dir, filename)
        if not os.path.exists(path):
            return f"[dim]✗ {label}[/dim]"

        try:
            with open(path, "r") as f:
                installed_content = f.read()
        except OSError:
            return f"[yellow]? {label} (couldn't read {path})[/yellow]"

        if installed_content == generator():
            return f"[green]✓ {label}[/green]"
        if installed_content.startswith(_LEGACY_HOOK_PREFIXES[filename]):
            return (
                f"[yellow]✓ {label} (older EnvShield hook -- may not cover every "
                "service; run 'envshield hook install' to upgrade)[/yellow]"
            )
        if ENVSHIELD_HOOK_MARKER in installed_content:
            return f"[yellow]✓ {label} (modified -- not EnvShield's own hook)[/yellow]"
        return f"[yellow]✓ {label} (not installed by EnvShield)[/yellow]"

    def print_hook_status(self) -> None:
        """Print the installed hooks and what they currently cover."""
        if not self.git_root:
            console.print("[dim]Not in a git repository.[/dim]")
            return

        # Import here to avoid circular imports (same pattern as
        # _do_install_hooks above).
        hooks_dir = git_utils.get_hooks_dir() or os.path.join(
            self.git_root, ".git", "hooks"
        )

        status = [
            self._hook_status_line(
                hooks_dir,
                "pre-commit",
                "pre-commit hook (secret scanning)",
                _generate_pre_commit_hook_content,
            ),
            self._hook_status_line(
                hooks_dir,
                "post-merge",
                "post-merge hook (config change detection)",
                _generate_post_merge_hook_content,
            ),
        ]

        console.print("\n[bold]Git Hooks:[/bold]")
        for s in status:
            console.print(f"  {s}")

        console.print("\n[bold]Coverage[/bold] [dim](from envshield.yml, now):[/dim]")
        try:
            coverage = schema_coverage()
        except EnvShieldException as e:
            console.print(f"  [red]Can't resolve coverage: {e}[/red]")
            console.print()
            return
        if not coverage:
            console.print(
                "  [dim]No services registered -- only secret scanning.[/dim]"
            )
        for schema, files, users in coverage:
            console.print(f"  {schema} → {', '.join(users)}")
            for base in files[1:]:
                console.print(f"    [dim]extends {os.path.relpath(base)}[/dim]")
        console.print()
