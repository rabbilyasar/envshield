# envshield/utils/git_utils.py
# Helper functions for interacting with the local Git repository.

import os
import subprocess
from typing import List, Optional


def get_git_root() -> Optional[str]:
    """
    Finds the root directory of the current Git repository.

    Returns:
        The absolute path to the Git root, or None if not in a Git repository.
    """
    try:
        # This git command returns the top-level directory path.
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fails if not in a git repo or if git is not installed.
        return None


def get_staged_files() -> List[str]:
    """
    Gets a list of all files that are currently staged for the next commit.

    Returns:
        A list of absolute paths to the staged files.
    """
    git_root = get_git_root()
    if not git_root:
        return []

    try:
        # This git command lists files that are added, copied, modified, or renamed.
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True,
            text=True,
            check=True,
        )
        # The output is relative to the git root, so we make it absolute.
        relative_paths = result.stdout.strip().split("\n")
        absolute_paths = [
            os.path.join(git_root, path) for path in relative_paths if path
        ]
        return absolute_paths
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def get_staged_file_content(file_path: str) -> Optional[str]:
    """
    Reads a file's content as it exists in the Git index (staged), not on disk.

    This matters because the working-tree copy can differ from what's staged:
    a file can be `git add`-ed with a secret, then edited on disk to remove it
    without re-staging. A hook that scans the filesystem would see the clean
    version and let the commit through, even though the secret is still what
    gets committed.

    Returns:
        The staged content, or None if it can't be read (not in a repo, the
        path isn't staged, or the blob is binary/undecodable).
    """
    git_root = get_git_root()
    if not git_root:
        return None

    relative_path = os.path.relpath(file_path, git_root)
    try:
        result = subprocess.run(
            ["git", "show", f":{relative_path}"],
            cwd=git_root,
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
