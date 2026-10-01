# envshield/core/source_files.py
"""
Which files in the working tree are project source: shared by 'scan',
'explain', service discovery, and the evaluator's code-reference pass, so
none of them decides "what counts as a file to read" differently.

Walk-time protections only (default-excluded directories, symlinked leaves
skipped). The read-time symlink boundary is `open_nofollow`.
"""

import fnmatch
import os
from typing import List

# Directories that are never useful to scan and are expensive/noisy to walk:
# dependency trees, VCS internals, virtualenvs, and build artifacts. These are
# always pruned in addition to whatever the user configures in envshield.yml.
#
# "vendor" was added after real-world scanning turned up FPs exclusively in
# third-party vendored code (a minified Private Key stub, a minified plugin
# bundle, and a vendored TypeScript .d.ts's type-signature parameters) with
# zero confirmed real credentials ever found under a vendor/ path across the
# validated corpus -- the same noisy-dependency-tree reasoning as
# node_modules above, not a new exclusion category. Matched by exact
# directory name (see is_default_excluded_dir), so "my_vendor" or
# "vendored" are untouched -- only a path component literally named
# "vendor" is pruned, at any depth.
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "vendor",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
}

# Files (and, for importer.py's default-value suggestion path, individual
# values) larger than this are skipped rather than fully read/embedded --
# reading a multi-MB file/value in full, or baking one into a generated
# artifact verbatim, is a real cost (and, for a generated schema, a
# correctness problem) with no proportional benefit. Named and exported so
# any other caller with the same "don't fully process something this
# large" concern reuses this exact threshold instead of picking its own.
MAX_SCANNABLE_SIZE_BYTES = 1_000_000

PYTHON_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
DISCOVERABLE_SUFFIXES = PYTHON_SUFFIXES + JS_SUFFIXES

# O_NOFOLLOW doesn't exist on Windows; checked once rather than per call.
_CAN_USE_O_NOFOLLOW = hasattr(os, "O_NOFOLLOW")


def is_default_excluded_dir(dirname: str) -> bool:
    return (
        dirname in DEFAULT_EXCLUDED_DIRS
        or dirname.endswith(".egg-info")
        or dirname.endswith(".dist-info")
    )


def open_nofollow(path: str):
    """
    Opens `path` for reading, refusing (OSError/ELOOP) if its final
    component is a symlink -- atomically, in the open() itself, so there is
    no check-then-open race. On a platform without O_NOFOLLOW (Windows)
    this is a plain open: an accepted, documented gap.
    """
    flags = os.O_RDONLY
    if _CAN_USE_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    return os.fdopen(fd, "r", encoding="utf-8", errors="ignore")


def discoverable_files(root_dir: str) -> List[str]:
    """
    Every .py/.js/.jsx/.ts/.tsx file under `root_dir` -- symlink-safe (a
    symlinked root or leaf is skipped) and excluded-dir-pruned. Paths are
    normalized (os.path.normpath), so a root of "." yields "app/x.py", not
    "./app/x.py", and the same file reached through two roots compares
    equal.
    """
    if os.path.islink(root_dir):
        return []
    files: List[str] = []
    for root, dirs, filenames in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not is_default_excluded_dir(d)]
        for filename in filenames:
            if not filename.endswith(DISCOVERABLE_SUFFIXES):
                continue
            file_path = os.path.join(root, filename)
            if os.path.islink(file_path):
                continue
            files.append(os.path.normpath(file_path))
    return files


def matches_exclusion(file_path: str, pattern: str) -> bool:
    """
    Whether `file_path` matches one `secret_scanning.exclude_files` glob.

    The path is compared relative to the project root, normalized: an
    absolute path, "./app/x.py" (a walk from "."), and "app/x.py" (a Git
    path) are the same file and must match the same patterns. A leading
    "**/" also matches zero directories, so "**/tests/*" excludes a
    top-level "tests/" too, as it always did for a full-tree walk.
    """
    rel = file_path
    if os.path.isabs(rel):
        rel = os.path.relpath(rel, os.getcwd())
    rel = os.path.normpath(rel)
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if fnmatch.fnmatch(rel, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])
