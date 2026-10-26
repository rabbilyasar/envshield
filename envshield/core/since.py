# envshield/core/since.py
"""
What changed since a revision, for 'check --since REV' (V0-2b, #42; D-13
in evaluator-decisions.md).

Three answers, all relative to REV and the live working tree:

- which files changed (changed_files): the only ones 'check --since' reads
  code references from, so a large repository isn't walked for a small
  change (BL-205, #85). A git failure is an error, never "no changes".
- which of a service's undeclared reads in those files were already there
  at REV. Only proven ones count: the gate ignores an old read, so
  guessing "old" would let a new one through. Anything not proven is new.
- how the service's contract changed (contract_diff), reported only.

Source text and schema metadata only -- never a configuration value.
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from envshield.config import manager as config_manager
from envshield.core import contract_diff, discovery, schema_snapshot, source_files
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


def changed_files(revision: str) -> List[str]:
    """
    Every path changed between `revision` and the working tree, plus every
    untracked (not ignored) file: absolute, deduplicated. Deleted paths are
    included, since their content at `revision` can prove a read old (a
    moved read). Raises when git can't answer: an empty list would read as
    "nothing changed", a false pass.
    """
    try:
        changed = git_utils.list_changed_files(revision, None, strict=True)
        untracked = git_utils.list_untracked_files(strict=True)
    except git_utils.GitError as e:
        raise EnvShieldException(
            f"Couldn't list the files changed since '{revision}': {e}"
        ) from e
    return list(dict.fromkeys(changed + untracked))


def read_before(
    service_name: str,
    revision: str,
    undeclared: Dict[str, List[Any]],
    changed: List[str],
) -> Set[str]:
    """
    The names in `undeclared` (name -> current high-confidence usages) that
    one of the `changed` files belonging to the service read at `revision`,
    found by check's own discovery (BaseSettings fields included). Identity
    is the variable name, so a read moved to another file is old. A file
    git can't show at `revision` (new, or a git failure) proves nothing.
    """
    if not undeclared:
        return set()
    from envshield.core import evaluator  # evaluator imports this module

    belongs = evaluator.service_source_filter(service_name)
    wanted = set(undeclared)
    proven: Set[str] = set()
    for path in changed:
        rel = os.path.normpath(os.path.relpath(path) if os.path.isabs(path) else path)
        if not belongs(rel):
            continue
        content = git_utils.get_file_content_at_revision(os.path.abspath(rel), revision)
        # Same cap as the current side; a skipped file proves nothing.
        if content is None or len(content) > source_files.MAX_SCANNABLE_SIZE_BYTES:
            continue
        usages, _dynamic = discovery.discover_references(content, rel)
        proven |= {u.variable for u in usages if u.confidence == "high"} & wanted
        if proven == wanted:
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
    service_name: str,
    revision: str,
    undeclared: Dict[str, List[Any]],
    changed: List[str],
) -> SinceResult:
    result = contract_changes(service_name, revision)
    result.read_before = read_before(service_name, revision, undeclared, changed)
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
