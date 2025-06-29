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


def resolve_symlink_target(symlink_path: Path) -> Path | None:
    """
    Resolves the target of a symbolic link.
    :param symlink_path: The path to the potential symbolic link.
    :return: The absolute path to the target if `symlink_path` is a symlink, otherwise None.
    """
    if symlink_path.is_symlink():
        try:
            target = symlink_path.resolve()
            return target
        except FileNotFoundError:
            print_warning(f"Symlink {symlink_path} points to a non-existent target.")
            return None
    return None

def create_symlink(source_path: Path, destination_path: Path):
    """
    Creates a symbolic link from `source_path` to `destination_path`.
    This function handles existing `destination_path` conflicts by:
    1. Removing an existing symbolic link.
    2. Backing up an existing regular file.
    3. Raising an error if `destination_path` is a directory or other unhandleable type.

    :param source_path: The absolute path to the original file/directory that the symlink will point to.
    :param destination_path: The absolute path where the symbolic link will be created.
    :raises FileOperationError: If the symlink cannot be created due to permissions,
                                or if `destination_path` is an unhandleable existing item.
    """
    if not source_path.exists():
        raise FileOperationError(f"Source file for symlink does not exist: '[path]{source_path}[/path]'")

    if destination_path.exists() or destination_path.is_symlink():
        if destination_path.is_symlink():
            # If it's an existing symlink, remove it to replace it with the new one.
            print_info(f"Removing existing symlink: '[path]{destination_path}[/path]'")
            try:
                destination_path.unlink() # Remove the symlink
            except OSError as e:
                raise FileOperationError(f"Failed to remove existing symlink: '[path]{destination_path}[/path]'. Error: {e}")
        elif destination_path.is_file():
            backup_path = destination_path.with_name(f"{destination_path.name}.envguard_bak")
            print_warning(f"Existing file '[path]{destination_path.name}[/path]' found. Backing up to '[path]{backup_path.name}[/path]'.")
            try:
                # Rename the existing file to the backup path
                destination_path.rename(backup_path)
            except OSError as e:
                raise FileOperationError(f"Failed to back up existing file: '[path]{destination_path}[/path]'. Error: {e}")
        else:
            # If it's a directory or other type of file system object, we cannot safely overwrite it.
            raise FileOperationError(
                f"Cannot create symlink: '[path]{destination_path}[/path]' exists and is not a file or existing symlink. "
                "Please move or delete it manually to proceed."
            )
    try:
        # Create the symbolic link
        # On Windows, creating symlinks typically requires Administrator privileges
        # or Developer Mode to be enabled. Without it, an OSError with specific error code occurs.
        os.symlink(source_path, destination_path)
    except OSError as e:
        if os.name == 'nt' and e.errno == 1314:  # ERROR_PRIVILEGE_NOT_HELD on Windows
            raise FileOperationError(
                f"Permission denied to create symlink for '[path]{destination_path}[/path]'. "
                "On Windows, you need administrator privileges or Developer Mode enabled to create symbolic links. "
                "Consider running your terminal as an administrator, or enable Developer Mode in Windows settings."
            ) from e
        # Re-raise any other OS-level errors during symlink creation
        raise FileOperationError(f"Failed to create symlink from '[path]{source_path}[/path]' to '[path]{destination_path}[/path]': {e}")

def get_platform_info() -> dict:
    """
    Returns a dictionary with information about the current platform.
    This includes the OS name, version, architecture, and Python version.

    :return: A dictionary containing platform information.
    """
    return {
        "system": platform.system(),     # e.g., 'Linux', 'Windows', 'Darwin'
        "node_name": platform.node(),    # Network name of the computer
        "release": platform.release(),   # OS release level (e.g., '5.15.0-76-generic')
        "version": platform.version(),   # OS version information
        'architecture': platform.architecture(),
        "machine": platform.machine(),   # Hardware architecture (e.g., 'x86_64')
        "python_version": sys.version,   # Full Python version string
        'python_executable': sys.executable,
        'python_platform': platform.platform(),
        'python_implementation': platform.python_implementation(),
        "envguard_version": __import__('envguard').__version__ # EnvGuard package version
    }

def add_to_gitignore(repo_root: Path, patterns: list[str]):
    """
    Adds specified patterns to the project's .gitignore file.
    Creates .gitignore if it doesn't exist. Ensures patterns are unique.

    :param repo_root: The root path of the Git repository.
    :param patterns: A list of patterns (e.g., '.env', '*.log') to add.
    :raises FileOperationError: If unable to write to .gitignore.
    """
    gitignore_path = repo_root / '.gitignore'
    existing_patterns = set()
    if gitignore_path.exists():
        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith('#'):
                        existing_patterns.add(stripped_line)
        except OSError as e:
            raise FileOperationError(f"Failed to read existing .gitignore at '[path]{gitignore_path}[/path]': {e}")

    # Add new patterns, ensuring uniqueness
    new_patterns_to_add = []
    for pattern in patterns:
        # Add a leading slash if not present for root-level files (e.g., '/.env')
        # This prevents accidental matching of files in subdirectories (e.g., myapp/.env)
        formatted_pattern = pattern if pattern.startswith('/') else f'/{pattern}'
        if formatted_pattern not in existing_patterns:
            new_patterns_to_add.append(formatted_pattern)
            existing_patterns.add(formatted_pattern) # Add to set to avoid duplicates if function is called multiple times

    if new_patterns_to_add:
        try:
            with open(gitignore_path, 'a', encoding='utf-8') as f:
                f.write("\n# EnvGuard additions\n")
                for pattern in new_patterns_to_add:
                    f.write(f"{pattern}\n")
            print_success(f"Added patterns to .gitignore: {', '.join(new_patterns_to_add)}")
        except OSError as e:
            raise FileOperationError(f"Failed to write to .gitignore at '[path]{gitignore_path}[/path]': {e}")

