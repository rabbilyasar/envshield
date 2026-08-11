# envshield/core/schema_snapshot.py
"""
Revision-aware schema loading for Phase 2A's Contract Diff.

This module owns exactly one thing: producing the same effective (post-
extends-merge) schema dict envshield.yml/env.schema.toml already produce
for the live working tree, but resolved as of an arbitrary Git revision
instead. Every read on the revision path goes through `git show
<revision>:<path>` -- never `open()`, never the live filesystem -- so a
historical result can never silently pick up a working-tree file. The
working-tree side itself (`revision=None`) is untouched: it delegates
straight to config_manager.load_schema, unchanged.

Deliberately NOT a generalized "virtual filesystem" or revision-object
abstraction -- just the small set of functions needed to go from
(service_name, revision) to one resolved schema dict, mirroring
config_manager's existing envshield.yml -> schema path -> extends-merge
pipeline step for step, with git-show substituted for disk reads and a
purely lexical (not realpath-based) containment check, since historical
Git blob content has no live filesystem to resolve symlinks against.
"""

import os
import posixpath
from typing import Any, Dict, Optional

import toml
import yaml

from envshield.config import manager as config_manager
from envshield.utils import git_utils

from .exceptions import (
    ConfigParseError,
    SchemaNotFoundError,
    SchemaParseError,
    UnsafePathError,
)


def load_schema_for_diff(service_name: str, revision: Optional[str]) -> Dict[str, Any]:
    """
    The one entry point Contract Diff's CLI layer calls for each side of a
    comparison. `revision=None` means the live working tree (unchanged
    behavior, delegated straight to config_manager.load_schema); any other
    value is treated as a Git revision and resolved entirely via
    `git show <revision>:<path>`.
    """
    if revision is None:
        return config_manager.load_schema(service_name=service_name)

    schema_path = _get_service_schema_path_at_revision(service_name, revision)
    if _read_optional_at_revision(schema_path, revision) is None:
        raise SchemaNotFoundError(
            f"Schema file not found: '{schema_path}' at revision '{revision}'."
        )
    return _load_schema_at_revision(schema_path, revision)


def _load_config_at_revision(revision: str) -> Dict[str, Any]:
    """
    envshield.yml as it existed at `revision`. Extracts nothing beyond
    what's needed to resolve one service's schema path -- not manifests,
    not other services, not any other top-level key that happens to live
    in the same file.
    """
    content = _read_at_revision(config_manager.CONFIG_FILE_NAME, revision)
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ConfigParseError(
            f"{config_manager.CONFIG_FILE_NAME} at revision '{revision}'", str(e)
        )
    return parsed if isinstance(parsed, dict) else {}


def _get_service_schema_path_at_revision(service_name: str, revision: str) -> str:
    config = _load_config_at_revision(revision)
    services = config.get("services")
    services = services if isinstance(services, dict) else {}

    if service_name not in services:
        available = ", ".join(sorted(services.keys())) or "none registered"
        raise SchemaNotFoundError(
            f"Service '{service_name}' not found in envshield.yml at revision "
            f"'{revision}'. Available there: {available}."
        )

    service_config = services[service_name]
    schema_path = (
        service_config.get("schema") if isinstance(service_config, dict) else None
    )
    if not schema_path:
        raise SchemaNotFoundError(
            f"Service '{service_name}' has no schema path in envshield.yml at "
            f"revision '{revision}'."
        )
    return _ensure_lexically_within_project(schema_path, revision)


def _load_schema_at_revision(
    schema_path: str, revision: str, _visited: Optional[frozenset] = None
) -> Dict[str, Any]:
    """
    Mirrors config_manager._load_schema_file's extends-merge semantics
    exactly (child overrides base; later 'extends' list entries override
    earlier ones; 'extends' itself never appears in the merged result) --
    every read, the schema itself and every 'extends' target alike, comes
    from `git show <revision>:<path>`, never from disk.
    """
    visited = _visited or frozenset()
    canonical_path = posixpath.normpath(schema_path)
    if canonical_path in visited:
        raise SchemaParseError(schema_path, "circular 'extends' chain detected")
    visited = visited | {canonical_path}

    raw = _parse_toml_at_revision(schema_path, revision)
    extends = raw.pop("extends", None)

    merged: Dict[str, Any] = {}
    if extends:
        base_refs = [extends] if isinstance(extends, str) else list(extends)
        base_dir = posixpath.dirname(schema_path) or "."
        for base_ref in base_refs:
            base_path = _ensure_lexically_within_project(
                posixpath.normpath(posixpath.join(base_dir, base_ref)), revision
            )
            if _read_optional_at_revision(base_path, revision) is None:
                raise SchemaNotFoundError(
                    f"'{schema_path}' extends '{base_path}', which doesn't exist "
                    f"at revision '{revision}'. Fix the 'extends' path, or "
                    f"compare against a revision where it exists."
                )
            merged.update(_load_schema_at_revision(base_path, revision, visited))

    merged.update(raw)
    return merged


def _parse_toml_at_revision(schema_path: str, revision: str) -> Dict[str, Any]:
    """
    Mirrors config_manager.load_toml_schema's friendly error formatting,
    duplicated rather than shared -- it's a few lines wrapping a different
    I/O source (in-memory content vs. a file handle), and this phase's own
    guidance is to avoid speculative refactoring for a handful of shared
    lines with intentionally different responsibilities.
    """
    content = _read_at_revision(schema_path, revision)
    try:
        return toml.loads(content)
    except toml.TomlDecodeError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            dup_key = (
                error_msg.split("What? ")[1].split(" ")[0]
                if "What? " in error_msg
                else "unknown"
            )
            details = (
                f"Duplicate key found: {dup_key}\n"
                f"Check the schema at revision '{revision}' for duplicate "
                f"[{dup_key}] definitions"
            )
        else:
            details = (
                error_msg.split("(line")[0].strip()
                if "(line" in error_msg
                else error_msg
            )
        raise SchemaParseError(schema_path, details)


def _read_at_revision(path: str, revision: str) -> str:
    content = _read_optional_at_revision(path, revision)
    if content is None:
        raise SchemaNotFoundError(f"'{path}' does not exist at revision '{revision}'.")
    return content


def _read_optional_at_revision(path: str, revision: str) -> Optional[str]:
    """
    The only place this module touches the filesystem at all is indirectly,
    through git_utils -- which itself only ever shells out to `git show`.
    There is deliberately no fallback to open()/os.path.exists() anywhere
    in this module: a historical dependency that can't be read from the
    revision is missing, full stop, never silently satisfied from disk.
    """
    absolute_path = os.path.join(os.getcwd(), path)
    return git_utils.get_file_content_at_revision(absolute_path, revision)


def _ensure_lexically_within_project(path: str, revision: str) -> str:
    """
    Deliberately NOT config_manager._ensure_within_project: that check is
    realpath/live-cwd based, which has no meaning for historical Git
    content -- there's no live filesystem to resolve a symlink against
    for a blob read out of a past revision. This is the lexical half only
    (reject '..'-escape and absolute paths), evaluated with `posixpath`
    exclusively so the result is deterministic regardless of the host
    platform, matching Git's own always-POSIX internal path semantics --
    never `os.path`, which would use backslash separators on Windows and
    silently misjudge containment for paths that are really Git paths.
    """
    if posixpath.isabs(path):
        raise UnsafePathError(f"path at revision '{revision}'", path, "<project root>")
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith("../"):
        raise UnsafePathError(f"path at revision '{revision}'", path, "<project root>")
    return path
