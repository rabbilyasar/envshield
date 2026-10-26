# envshield/core/since.py
"""
What changed since a revision, for 'check --since REV' (V0-2b, #42; D-13
in evaluator-decisions.md).

Two answers, both relative to REV and the live working tree:

- which of a service's undeclared reads were already there at REV. Only
  proven ones count: the gate ignores an old read, so guessing "old" would
  let a new one through. Anything not proven is new.
- how the service's contract changed (contract_diff), reported only.

Source text and schema metadata only -- never a configuration value.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from envshield.config import manager as config_manager
from envshield.core import (
    contract_diff,
    dependency_snapshot,
    schema_snapshot,
    source_files,
)
from envshield.core.exceptions import EnvShieldException, SchemaNotFoundError
from envshield.utils import git_utils


@dataclass
class SinceResult:
    revision: str
    # Undeclared names proven to be read at `revision` already.
    read_before: Set[str] = field(default_factory=set)
    # None when the contract couldn't be compared (contract_introduced, or
    # an error).
    contract: Optional[contract_diff.ContractDiff] = None
    # No contract for this service existed at `revision`.
    contract_introduced: bool = False
    # Why the contract couldn't be compared; reads are still judged.
    contract_error: Optional[str] = None


def _unchanged_since(path: str, revision: str) -> bool:
    """Whether `path` on disk has exactly the content it had at `revision`.
    Any doubt (unreadable, absent there, git failing) is False."""
    try:
        with source_files.open_nofollow(path) as f:  # never through a symlink
            current = f.read()
    except OSError:
        return False
    before = git_utils.get_file_content_at_revision(os.path.abspath(path), revision)
    return before is not None and before == current


def read_before(
    service_name: str, revision: str, undeclared: Dict[str, List[Any]]
) -> Set[str]:
    """
    The names in `undeclared` (name -> current high-confidence usages)
    proven to be read at `revision`: 'undeclared's own comparison finds
    them there (same identity, the variable name; renames included), or one
    of their current reads is in a file unchanged since `revision`.
    """
    if not undeclared:
        return set()
    try:
        usages_a, _ = dependency_snapshot.discover_usages_for_service(
            service_name, revision, dependency_snapshot.DEFAULT_REVISION_B, quiet=True
        )
    except EnvShieldException:
        usages_a = []
    proven = {u.variable for u in usages_a} & set(undeclared)
    unchanged: Dict[str, bool] = {}
    for name, usages in undeclared.items():
        if name in proven:
            continue
        for usage in usages:
            path = usage.file_path
            if path not in unchanged:
                unchanged[path] = _unchanged_since(path, revision)
            if unchanged[path]:
                proven.add(name)
                break
    return proven


def contract_changes(service_name: str, revision: str) -> SinceResult:
    """The service's contract at `revision` against the live one."""
    result = SinceResult(revision=revision)
    try:
        try:
            before = schema_snapshot.load_schema_view_for_diff(service_name, revision)
        except SchemaNotFoundError:
            result.contract_introduced = True
            return result
        now = schema_snapshot.load_schema_view_for_diff(service_name, None)
        result.contract = contract_diff.diff_schemas(
            before.fields,
            now.fields,
            set(before.system),
            set(now.system),
            environments=config_manager.get_environments(),
        )
    except EnvShieldException as e:
        result.contract_error = str(e)
    return result


def evaluate(
    service_name: str, revision: str, undeclared: Dict[str, List[Any]]
) -> SinceResult:
    result = contract_changes(service_name, revision)
    result.read_before = read_before(service_name, revision, undeclared)
    return result


# --- findings (the D-9 shape) --------------------------------------------


def change_detail(change: contract_diff.ContractChange) -> str:
    # A default_changed description quotes both defaults; a finding never
    # carries a value, so it names the change only ('schema diff' has it).
    if change.category == "default_changed":
        return f"'{change.variable}' default changed."
    return change.description


def _change_next_step(blocking: bool, service_name: str) -> Dict[str, str]:
    if not blocking:
        return {
            "by": "agent",
            "text": "None needed: configuration valid before stays valid.",
        }
    return {
        "by": "human",
        "text": (
            "A person should review this contract change before it merges: "
            f"every local file and deploy of '{service_name}' must still "
            "satisfy the contract."
        ),
    }


def contract_findings(
    result: SinceResult, service_name: str, fields: Dict[str, Any], schema_path: str
) -> List[Dict[str, Any]]:
    """One finding per contract change. Reported only: verdict is None, so
    none of them changes a verdict or the gate (D-13)."""
    if result.contract is None:
        return []
    findings = []
    for change in result.contract.changes:
        blocking = contract_diff.is_blocking_change(change)
        field_schema = fields.get(change.variable) or {}
        findings.append(
            {
                "kind": "contract_change",
                "verdict": None,
                "reason_code": change.category,
                "detail": change_detail(change),
                "variable": change.variable,
                "service": service_name,
                "location": {"file": schema_path, "line": None, "container": None},
                "secret": bool(field_schema.get("secret", False)),
                "blocking": blocking,
                "next_step": _change_next_step(blocking, service_name),
            }
        )
    return findings
