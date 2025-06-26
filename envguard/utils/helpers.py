import subprocess
import sys
import os
from pathlib import Path
import stat
import platform
import shutil

from envguard.utils.error_handling import GitError, FileOperationError, EnvGuardError
from envguard.utils.display import print_warning, print_info, print_success


def is_inside_git_repo(path: Path = None) -> Path | None:
    """
    Checks if the given path (or current working directory if None) is inside a Git repository.

    This function uses `git rev-parse --show-toplevel` which is a reliable way
    to find the root of a Git repository from any subdirectory.

    :param path: The starting path to check from. Defaults to Path.cwd().
    :return: The absolute Path to the Git repository root if found, otherwise None.
    :raises GitError: If the 'git' command is not found on the system.
    """
    if path is None:
        path = Path.cwd()

    try:
        result  = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True, # Capture stdout and stderr
            env=os.environ,  # Use the current environment variables
            shell=False,  # Avoid shell injection vulnerabilities
            check=True,  # Raise CalledProcessError if the command returns a non-zero exit code
            text=True, # Decode stdout/stderr as text
            cwd=path, # Execute the command from the specified path
            stdout=subprocess.PIPE, # Capture stdout
            stderr=subprocess.PIPE, # Capture stderr
        )
        return Path(result.stdout.strip()).resolve()
    except subprocess.CalledProcessError:
        # This occurs if `git rev-parse` exits with a non-zero status (i.e., not a Git repo)
        return None
    except FileNotFoundError:
        # This occurs if the 'git' command is not found
        raise GitError("Git command not found. Please ensure Git is installed and available in your PATH.")
    except Exception as e:
        # Catch any other exceptions and raise a generic GitError
        raise GitError(f"An unexpected error occurred while checking for a Git repository: {e}")


def get_git_staged_files(repo_path: Path) -> list[Path]:
    """
    Returns a list of file paths that are currently staged for commit in a Git repository.
    Paths are returned as absolute paths.

    This uses `git diff --cached --name-only` to list files in the staging area.
    The `--diff-filter` ensures we only get files that represent content changes (added, modified, etc.).

    :param repo_root: The root path of the Git repository.
    :return: A list of absolute Paths to staged files.
    :raises GitError: If Git commands fail or the Git executable is not found.
    """
    try:
        # --cached: inspects the staging area (index)
        # --name-only: shows only the names of affected files
        # --diff-filter=ACMRTUXB: filter to show only files that are Added, Copied, Modified, Renamed, Type changed, Updated, eXists, Broken
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMRTUXB'],
            capture_output=True,
            text=True,
            cwd=repo_path, # Execute command from repo root to get relative paths correctly
            check=True
        )
        # Split lines, strip whitespace, filter empty lines, and convert to absolute paths.
        staged_files_relative = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [repo_path / f for f in staged_files_relative]
    except subprocess.CalledProcessError as e:
        raise GitError(f"Failed to retrieve staged files from Git: {e.stderr.strip()}")
    except FileNotFoundError:
        raise GitError("Git command not found. Please ensure Git is installed and in your system's PATH.")
    except Exception as e:
        raise GitError(f"An unexpected error occurred while retrieving staged files: {e}")