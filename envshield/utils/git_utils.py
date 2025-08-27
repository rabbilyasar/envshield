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
