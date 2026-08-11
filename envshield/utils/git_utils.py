# envshield/utils/git_utils.py
# Helper functions for interacting with the local Git repository.

import os
import subprocess


def get_git_root() -> str | None:
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


def get_hooks_dir() -> str | None:
    """
    Returns the directory Git will actually invoke hooks from: whatever
    'core.hooksPath' is configured to (e.g. by Husky or a similar tool), or
    the repository's default '.git/hooks' if that's unset.

    Installing/checking hooks under '.git/hooks' unconditionally -- ignoring
    a configured core.hooksPath -- silently installs a hook Git never runs,
    and 'doctor' would report it as active even though it isn't.

    Returns:
        The absolute path to the hooks directory, or None if not in a Git
        repository.
    """
    git_root = get_git_root()
    if not git_root:
        return None

    try:
        result = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=True,
        )
        hooks_path = result.stdout.strip()
        if hooks_path:
            return (
                hooks_path
                if os.path.isabs(hooks_path)
                else os.path.abspath(os.path.join(git_root, hooks_path))
            )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return os.path.join(git_root, ".git", "hooks")


def get_ignored_files(paths: list[str]) -> set[str]:
    """
    Returns the subset of `paths` that Git would ignore (per .gitignore),
    via a single batched 'git check-ignore --stdin' call rather than one
    subprocess per file.

    Returns an empty set outside a Git repository, or if git itself isn't
    available -- callers should treat that as "can't tell, don't filter,"
    not "nothing is ignored."
    """
    if not get_git_root() or not paths:
        return set()

    try:
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(paths),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return set()
    # Exit code 1 just means "none of these are ignored" -- not an error.
    return {line for line in result.stdout.splitlines() if line}


def get_staged_files() -> list[str]:
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


def get_staged_file_content(file_path: str) -> str | None:
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


def get_head_file_content(file_path: str) -> str | None:
    """
    Reads a file's content from HEAD (the last committed version).

    Used for diff-aware scanning: compares HEAD vs staged to detect new lines.

    Returns:
        The file content from HEAD, or None if the file doesn't exist in HEAD
        (brand new file), can't be read, or is binary/undecodable.
    """
    git_root = get_git_root()
    if not git_root:
        return None

    relative_path = os.path.relpath(file_path, git_root)
    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{relative_path}"],
            cwd=git_root,
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # File doesn't exist in HEAD (brand new file) or git error
        return None


def revision_exists(revision: str) -> bool:
    """
    Whether `revision` resolves to a real commit.

    'git diff'/'git show' both treat a malformed or non-existent revision
    expression as "no output" rather than a distinguishable error in every
    call site above that already tolerates a failure (list_changed_files
    returns [], get_file_content_at_revision returns None) -- correct for
    'no changes'/'file missing there', but that same silence would make an
    outright bad revision read as "nothing changed" instead of "that
    revision doesn't exist." This primitive exists for callers that need to
    tell those two apart explicitly, rather than let the ambiguity stand.

    Returns False outside a Git repository or if git isn't available.
    """
    git_root = get_git_root()
    if not git_root:
        return False
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
            cwd=git_root,
            capture_output=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def list_changed_files(revision_a: str | None, revision_b: str | None) -> list[str]:
    """
    Lists paths that differ between `revision_a` and `revision_b` via
    'git diff --no-renames --name-only'. Either side may be None to mean
    "the current working tree" (git's own single-ref diff behavior); if
    both are None there's nothing meaningful to diff (working tree against
    the index isn't "changed files" for this primitive's callers), so that
    returns an empty list without shelling out at all.

    '--no-renames' is explicit rather than left to the user's
    'diff.renames' git config default: a renamed file would otherwise be
    reported differently across environments, making a caller's "what
    changed" answer non-deterministic. With it, a rename is deterministically
    reported as its old path removed and its new path added.

    Returns absolute paths (like get_staged_files), an empty list outside a
    Git repository, if git itself isn't available, or if a given revision
    doesn't resolve.
    """
    git_root = get_git_root()
    if not git_root:
        return []
    if revision_a is None and revision_b is None:
        return []

    revisions = [r for r in (revision_a, revision_b) if r is not None]
    try:
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", *revisions],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    relative_paths = [line for line in result.stdout.splitlines() if line]
    return [os.path.join(git_root, path) for path in relative_paths]


def list_untracked_files() -> list[str]:
    """
    Lists files present on disk but not tracked by Git and not ignored, via
    'git ls-files --others --exclude-standard'.

    'git diff' never reports these under any revision pair -- an untracked
    file has no committed or staged blob to diff against at all -- so a
    caller comparing a revision against the live working tree needs this
    separately to see a brand-new, not-yet-'git add'-ed file.

    Returns absolute paths (like get_staged_files), or an empty list
    outside a Git repository or if git isn't available.
    """
    git_root = get_git_root()
    if not git_root:
        return []

    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=git_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    relative_paths = [line for line in result.stdout.splitlines() if line]
    return [os.path.join(git_root, path) for path in relative_paths]


def get_file_content_at_revision(file_path: str, revision: str) -> str | None:
    """
    Reads a file's content as it existed at an arbitrary Git revision (a
    branch, tag, SHA, or any other ref/expression 'git show' itself
    accepts) -- the same primitive as get_head_file_content and
    get_staged_file_content, generalized to a caller-supplied ref instead
    of a hardcoded 'HEAD' or the staged index.

    Returns:
        The file's content at that revision, or None if it doesn't exist
        there, the revision itself doesn't resolve, or the blob is
        binary/undecodable.
    """
    git_root = get_git_root()
    if not git_root:
        return None

    relative_path = os.path.relpath(file_path, git_root)
    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{relative_path}"],
            cwd=git_root,
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="ignore")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
