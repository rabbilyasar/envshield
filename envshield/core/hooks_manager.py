"""Manages Git hooks installation and lifecycle."""

import os
import sys
from typing import Tuple

import questionary
from rich.console import Console

from ..core.exceptions import EnvShieldException
from ..utils import git_utils

console = Console()


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
            # Import here to avoid circular imports
            from . import scanner

            pre_commit, post_merge = self.are_hooks_installed()
            if not pre_commit:
                scanner.install_pre_commit_hook(force=force, non_interactive=False)
            if not post_merge:
                scanner.install_post_merge_hook(force=force, non_interactive=False)
        except EnvShieldException as e:
            console.print(f"[bold yellow]⚠️  Warning:[/] {e}")

    def _hook_status_line(
        self, hooks_dir: str, filename: str, label: str, generator
    ) -> str:
        """
        Describes one installed hook: missing, up to date, or stale --
        present, and still carrying EnvShield's marker/shape, but no longer
        byte-for-byte what EnvShield would generate right now (e.g. a
        service was added to envshield.yml after this hook was installed,
        so it's missing that service's block; or the file was hand-edited).

        This is purely informational: unlike `are_hooks_installed` (used by
        `hook install`/`uninstall`'s exact-match ownership check -- see
        BL-119/BL-120), staleness here never gates or changes what those
        commands do. A stale hook is not "not installed" -- it still runs
        and still protects whatever it was generated to protect; this only
        surfaces that it may no longer match the project's current config,
        so nothing here needed to touch that exact-match safety invariant.
        """
        path = os.path.join(hooks_dir, filename)
        if not os.path.exists(path):
            return f"[dim]✗ {label}[/dim]"

        try:
            with open(path, "r") as f:
                installed_content = f.read()
        except OSError:
            return f"[green]✓ {label}[/green]"

        if installed_content == generator():
            return f"[green]✓ {label}[/green]"

        return (
            f"[yellow]✓ {label} (stale -- doesn't match what EnvShield would "
            "generate now; run 'envshield hook install --yes' to refresh)[/yellow]"
        )

    def print_hook_status(self) -> None:
        """Print the current status of installed hooks."""
        if not self.git_root:
            console.print("[dim]Not in a git repository.[/dim]")
            return

        # Import here to avoid circular imports (same pattern as
        # _do_install_hooks above).
        from . import scanner

        hooks_dir = git_utils.get_hooks_dir() or os.path.join(
            self.git_root, ".git", "hooks"
        )

        status = [
            self._hook_status_line(
                hooks_dir,
                "pre-commit",
                "pre-commit hook (secret scanning)",
                scanner._generate_pre_commit_hook_content,
            ),
            self._hook_status_line(
                hooks_dir,
                "post-merge",
                "post-merge hook (config change detection)",
                scanner._generate_post_merge_hook_content,
            ),
        ]

        console.print("\n[bold]Git Hooks:[/bold]")
        for s in status:
            console.print(f"  {s}")
        console.print()
