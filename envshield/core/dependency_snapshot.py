# envshield/core/dependency_snapshot.py
"""
Revision-aware source discovery for Phase 2C's Source-to-Contract Change
Analysis.

Owns exactly the I/O side: resolving which source files changed between
two points (a concrete Git revision pair, or the default HEAD-vs-working-
tree comparison), reading each file's content from the right place for
each side, and handing that content to discovery.py (Phase 2B, unmodified)
to get back normalized usage records. Contains no schema/diff/
classification logic at all -- see dependency_diff.py for the pure
comparison this feeds.

Mirrors schema_snapshot.py's own revision=None-means-working-tree
convention, generalized one step further: schema_snapshot only ever reads
the working tree via config_manager (never touches git for that side, and
never needs an untracked-file list, since a schema is exactly one known
path). This module reads arbitrary sets of file paths discovered from git
itself, so its working-tree side additionally has to account for files
'git diff' structurally can't see at all -- untracked ones (see
git_utils.list_untracked_files).

Unlike schema_diff's symmetric (working tree, HEAD) default, this module's
default direction is fixed: HEAD is always the "old" side, the working
tree is always the "new" side being checked for a freshly introduced
dependency -- the "catch it before you commit" case CLAUDE.md's own
worked example describes.
"""

from typing import List, Optional, Tuple

from envshield.config import manager as config_manager
from envshield.utils import git_utils

from . import discovery

DEFAULT_REVISION_A = "HEAD"
DEFAULT_REVISION_B = None

_PYTHON_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
_DISCOVERABLE_SUFFIXES = _PYTHON_SUFFIXES + _JS_SUFFIXES


def _read_source(path: str, revision: Optional[str]) -> Optional[str]:
    """
    `revision=None` reads straight from disk -- uncommitted edits (staged
    or not) and untracked files alike, since none of those have a git
    object to read via 'git show' in the first place. Any other value
    reads via git_utils.get_file_content_at_revision, exactly like
    schema_snapshot's own working-tree/revision branch.

    Returns None if the file doesn't exist on that side (deleted, or not
    yet created) -- never raises, matching discovery.py's own
    never-raises contract for the content it's handed.
    """
    if revision is None:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except (IOError, OSError):
            return None
    return git_utils.get_file_content_at_revision(path, revision)


def _discover(
    content: Optional[str], file_path: str
) -> List[discovery.DiscoveredVariableUsage]:
    if content is None:
        return []
    if file_path.endswith(_PYTHON_SUFFIXES):
        return discovery.discover_python_usages(content, file_path)
    if file_path.endswith(_JS_SUFFIXES):
        return discovery.discover_js_usages(content, file_path)
    return []


def _changed_source_files(
    revision_a: Optional[str], revision_b: Optional[str]
) -> List[str]:
    """
    Every file path that could plausibly carry a new dependency between
    `revision_a` and `revision_b` -- git's own tracked-file diff, plus
    (only when `revision_b` is the live working tree) every untracked
    file, since 'git diff' never reports those under any revision pair.

    Deduplicated via dict.fromkeys rather than a set, to keep git's own
    ordering deterministic and testable.
    """
    changed = git_utils.list_changed_files(revision_a, revision_b)
    if revision_b is None:
        changed = changed + git_utils.list_untracked_files()
    return list(dict.fromkeys(changed))


def discover_usages_for_service(
    service_name: str,
    revision_a: Optional[str] = DEFAULT_REVISION_A,
    revision_b: Optional[str] = DEFAULT_REVISION_B,
) -> Tuple[
    List[discovery.DiscoveredVariableUsage], List[discovery.DiscoveredVariableUsage]
]:
    """
    The one public entry point: returns (usages_at_a, usages_at_b) for
    every changed/untracked source file that belongs to `service_name`'s
    own directory -- never another service's files, even when they also
    changed in the same diff. Without this filter, a repo-wide changed-
    file list checked against one service's schema would false-flag any
    other service's own (correctly declared, in its own schema) variables
    as missing from this one.

    Raises SchemaNotFoundError if `service_name` isn't registered.
    """
    service_dir = config_manager.get_service_dir(service_name)
    files = [
        f
        for f in _changed_source_files(revision_a, revision_b)
        if f.endswith(_DISCOVERABLE_SUFFIXES)
        and config_manager.service_dir_contains(f, service_dir)
    ]

    usages_a: List[discovery.DiscoveredVariableUsage] = []
    usages_b: List[discovery.DiscoveredVariableUsage] = []
    for file_path in files:
        usages_a.extend(_discover(_read_source(file_path, revision_a), file_path))
        usages_b.extend(_discover(_read_source(file_path, revision_b), file_path))

    return usages_a, usages_b
