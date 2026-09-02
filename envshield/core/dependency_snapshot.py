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

Symlink hardening (2026-08-13): reading arbitrary changed/untracked files
off disk is exactly the P0-2 threat class scanner.py already defends
against -- a symlink can point anywhere, and this module's working-tree
side would otherwise follow it and report a "dependency" sourced from
outside the project. See _changed_source_files (collection-time skip) and
_open_disk_source (the actual read-time boundary), both mirroring
scanner.py's existing _collect_files_to_scan/_open_for_scan exactly.
"""

import os
from typing import List, Optional, Tuple

from rich.console import Console

from envshield.config import manager as config_manager
from envshield.utils import git_utils

from . import discovery

console = Console()

DEFAULT_REVISION_A = "HEAD"
DEFAULT_REVISION_B = None

_PYTHON_SUFFIXES = (".py",)
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
_DISCOVERABLE_SUFFIXES = _PYTHON_SUFFIXES + _JS_SUFFIXES

# O_NOFOLLOW doesn't exist in the os module at all on Windows -- see
# scanner.py's identical module-level flag and _open_for_scan, which this
# mirrors exactly. Checked once at import time rather than per call.
_CAN_USE_O_NOFOLLOW = hasattr(os, "O_NOFOLLOW")


def _open_disk_source(path: str):
    """
    Opens a working-tree file for reading -- the actual security boundary
    against a symlink that appears after _changed_source_files's own
    islink() pre-filter already ran and let the path through (a real, if
    narrow, TOCTOU window between that check and this open). Mirrors
    scanner.py's _open_for_scan exactly, same mechanism and same rationale:
    duplicated rather than imported, since scanner.py is a sibling module
    with its own CLI-adjacent dependencies this module has no other reason
    to pull in.

    On a platform with O_NOFOLLOW, the kernel refuses to open the path if
    its *final* path component is a symlink, atomically with the open
    itself -- raises OSError (ELOOP) if so, which _read_source's caller
    already treats as "unreadable, return None," not a crash.

    On a platform without O_NOFOLLOW (Windows), falls back to a plain open
    with no symlink protection -- the same accepted, documented gap
    scanner.py's own fallback has.
    """
    flags = os.O_RDONLY
    if _CAN_USE_O_NOFOLLOW:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    return os.fdopen(fd, "r", encoding="utf-8", errors="ignore")


def _read_source(path: str, revision: Optional[str]) -> Optional[str]:
    """
    `revision=None` reads straight from disk -- uncommitted edits (staged
    or not) and untracked files alike, since none of those have a git
    object to read via 'git show' in the first place. Any other value
    reads via git_utils.get_file_content_at_revision, exactly like
    schema_snapshot's own working-tree/revision branch. A revision-based
    read never touches the local disk's symlink state at all -- git stores
    a symlink blob as target-path text, not the target's bytes -- so only
    the disk-read branch needs _open_disk_source's protection.

    Returns None if the file doesn't exist on that side (deleted, not yet
    created, or -- as of the symlink hardening -- refused by
    _open_disk_source) -- never raises, matching discovery.py's own
    never-raises contract for the content it's handed.
    """
    if revision is None:
        try:
            with _open_disk_source(path) as f:
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
    revision_a: Optional[str], revision_b: Optional[str], quiet: bool = False
) -> List[str]:
    """
    Every file path that could plausibly carry a new dependency between
    `revision_a` and `revision_b` -- git's own tracked-file diff, plus
    (only when `revision_b` is the live working tree) every untracked
    file, since 'git diff' never reports those under any revision pair.

    When `revision_b` is the live working tree, any path that's currently
    a symlink is skipped here too, mirroring scanner.py's
    _collect_files_to_scan exactly: a symlink can point anywhere on disk,
    and _read_source's later disk-read for this same path would otherwise
    follow it. Not applied for the explicit two-revision form -- neither
    side is a disk read there (both go through git_utils.
    get_file_content_at_revision, which reads a committed blob regardless
    of the working tree's current, unrelated disk state for that path), so
    a live symlink carries no risk to check for.

    Deduplicated via dict.fromkeys rather than a set, to keep git's own
    ordering deterministic and testable.
    """
    changed = git_utils.list_changed_files(revision_a, revision_b)
    if revision_b is None:
        changed = changed + git_utils.list_untracked_files()
    files = list(dict.fromkeys(changed))

    if revision_b is None:
        skipped_symlinks = sorted(f for f in files if os.path.islink(f))
        if skipped_symlinks:
            files = [f for f in files if f not in skipped_symlinks]
            if not quiet:
                console.print(
                    f"[dim]ℹ️  Skipping {len(skipped_symlinks)} symlink(s) -- not "
                    "followed, to avoid reading working-tree content from "
                    f"outside the project: {', '.join(skipped_symlinks)}[/dim]"
                )

    return files


def discover_usages_for_service(
    service_name: str,
    revision_a: Optional[str] = DEFAULT_REVISION_A,
    revision_b: Optional[str] = DEFAULT_REVISION_B,
    quiet: bool = False,
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

    `quiet` suppresses the console warning _changed_source_files prints
    when it skips a symlink -- callers rendering '--json' must pass
    quiet=True, or that warning's plain text would land in what's supposed
    to be a single, pure JSON document on stdout.

    Raises SchemaNotFoundError if `service_name` isn't registered.

    Known, deliberately-not-fixed Milestone-1 limitation: `service_dir`
    below always comes from the *live* envshield.yml
    (config_manager.get_service_dir), never a revision-specific one --
    unlike schema_snapshot.py, which fully resolves envshield.yml itself at
    an arbitrary revision. For the default HEAD-vs-working-tree comparison
    this is harmless (the live config is the relevant one). For the
    explicit two-revision form, if a service's directory has moved between
    the two revisions being compared, a file that genuinely belonged to
    the service at one of those revisions but not under the *current*
    mapping is silently excluded from both sides -- see
    TestServiceDirIsNotRevisionAware in test_dependency_snapshot.py for a
    concrete, locked-in reproduction. Left as an accepted limitation for
    this hardening pass rather than fixed: closing it properly would mean
    schema_snapshot-style revision-aware envshield.yml loading, which is
    out of scope here. The same live-config caveat applies to
    `additional_source_roots` below, for the same reason.

    BL-106: a service's own directory isn't always the whole of its
    discovery scope -- `additional_source_roots` (config_manager.
    get_service_additional_source_roots) names extra directories (e.g. a
    shared internal library outside every service's own directory) that
    also count toward this service. A file is included if it falls under
    *any* of these roots; since `_changed_source_files` already returns a
    deduplicated flat file list and this is a single membership test per
    file (not a per-root sub-scan), no additional deduplication is needed
    here even when a root overlaps or nests inside another.
    """
    service_dir = config_manager.get_service_dir(service_name)
    roots = [service_dir] + config_manager.get_service_additional_source_roots(
        service_name
    )
    files = [
        f
        for f in _changed_source_files(revision_a, revision_b, quiet=quiet)
        if f.endswith(_DISCOVERABLE_SUFFIXES)
        and any(config_manager.service_dir_contains(f, root) for root in roots)
    ]

    usages_a: List[discovery.DiscoveredVariableUsage] = []
    usages_b: List[discovery.DiscoveredVariableUsage] = []
    for file_path in files:
        # _changed_source_files returns absolute paths (git_utils'
        # documented contract, shared with get_staged_files) -- needed as-is
        # for _read_source's disk/git-show reads, but reported verbatim they
        # produced an unreadably long, Rich-table-truncated absolute path in
        # both 'undeclared's table and its --json output. The recorded
        # usage's own file_path is normalized to cwd-relative for display;
        # the read itself still uses the real, resolvable absolute path.
        display_path = os.path.relpath(file_path, os.getcwd())
        usages_a.extend(_discover(_read_source(file_path, revision_a), display_path))
        usages_b.extend(_discover(_read_source(file_path, revision_b), display_path))

    return usages_a, usages_b
