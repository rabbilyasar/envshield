"""Manages Git hooks installation and lifecycle."""

import os
from typing import Tuple

import questionary
from rich.console import Console

from ..core.exceptions import EnvShieldException
from ..utils import git_utils

console = Console()


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

        Args:
            force: If True, overwrite existing hooks.
        """
        try:
            # Import here to avoid circular imports
            from . import scanner

            scanner.install_pre_commit_hook(force=force, non_interactive=False)
            scanner.install_post_merge_hook(force=force, non_interactive=False)
        except EnvShieldException as e:
            console.print(f"[bold yellow]⚠️  Warning:[/] {e}")

    def print_hook_status(self) -> None:
        """Print the current status of installed hooks."""
        if not self.git_root:
            console.print("[dim]Not in a git repository.[/dim]")
            return

        pre_commit, post_merge = self.are_hooks_installed()

        status = []
        if pre_commit:
            status.append("[green]✓ pre-commit hook[/green] (secret scanning)")
        else:
            status.append("[dim]✗ pre-commit hook[/dim]")

        if post_merge:
            status.append("[green]✓ post-merge hook[/green] (config change detection)")
        else:
            status.append("[dim]✗ post-merge hook[/dim]")

        console.print("\n[bold]Git Hooks:[/bold]")
        for s in status:
            console.print(f"  {s}")
        console.print()
