# envshield/core/dependency_diff.py
"""
Source-to-Contract Change Analysis (Phase 2C): pure comparison between two
sets of discovered source usages, classified against a schema's declared
variables.

Takes only DiscoveredVariableUsage lists and a schema variable set -- no
git, no filesystem, no CLI. Mirrors contract_diff.py's shape (a Change
dataclass plus a Report wrapper carrying one summary flag), the same shape
every diff-like result in this codebase uses.

Every field here is a variable *name*, a file path, a line number, or a
free-text language/access-type label -- never a value. Unlike
contract_diff.py (which can carry a schema field's defaultValue),
dependency_diff.py has no value in scope at any point: discovery.py itself
never reads or reports what an environment variable is actually set to,
only that something reads a variable by that name. There is nothing here
that needs masking.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .discovery import DiscoveredVariableUsage

# Deliberately just two: unlike contract_diff's six-category schema-only
# taxonomy, a source usage is either already declared or it isn't -- there's
# no equivalent of "informational"/"requires_review" here, since discovery
# never has partial/conditional information the way a schema's requiredIf
# does.
CATEGORIES = frozenset({"missing_declaration", "declared"})


@dataclass
class DependencyChange:
    variable: str
    file_path: str
    line: int
    language: str
    access_type: str
    category: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variable": self.variable,
            "file_path": self.file_path,
            "line": self.line,
            "language": self.language,
            "access_type": self.access_type,
            "category": self.category,
        }


@dataclass
class DependencyChangeReport:
    changes: List[DependencyChange]

    @property
    def has_missing_declarations(self) -> bool:
        return any(c.category == "missing_declaration" for c in self.changes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_missing_declarations": self.has_missing_declarations,
            "changes": [c.to_dict() for c in self.changes],
        }


def find_new_usages(
    usages_a: List[DiscoveredVariableUsage], usages_b: List[DiscoveredVariableUsage]
) -> List[DiscoveredVariableUsage]:
    """
    Every usage in `usages_b` whose `variable` didn't already exist
    anywhere in `usages_a`. Identity is the variable name alone --
    deliberately excluding file_path, access_type, and line: switching
    os.environ.get("FOO") to os.environ["FOO"], a call site moving to a
    different line, or a whole file being moved/renamed (which
    git_utils.list_changed_files reports as a delete-and-add, not a
    rename) must not be reported as a new dependency. A second,
    independently new call site for a variable already used elsewhere in
    the project -- whether in the same file or a different one -- is an
    accepted non-detection of this identity choice.
    """
    existing_variables = {u.variable for u in usages_a}
    return [u for u in usages_b if u.variable not in existing_variables]


def classify_against_schema(
    new_usages: List[DiscoveredVariableUsage], schema_vars: Set[str]
) -> DependencyChangeReport:
    """
    The one public entry point after find_new_usages: labels each new
    usage 'missing_declaration' (not in the schema) or 'declared' (already
    is). Both are always returned -- a caller wanting only the actionable
    ones filters on category, rather than this module silently deciding
    what's worth surfacing (the same presentation/domain split
    contract_diff.py already follows).
    """
    changes = [
        DependencyChange(
            variable=usage.variable,
            file_path=usage.file_path,
            line=usage.line,
            language=usage.language,
            access_type=usage.access_type,
            category=(
                "declared" if usage.variable in schema_vars else "missing_declaration"
            ),
        )
        for usage in new_usages
    ]
    return DependencyChangeReport(changes=changes)


# SARIF 2.1.0 -- see https://docs.oasis-open.org/sarif/sarif/v2.1.0/. One
# stable rule: an undeclared read is the only thing this command treats as
# an actionable finding at all (see CATEGORIES above), so there is nothing
# else for a rule taxonomy to distinguish.
_SARIF_RULE_ID = "undeclared-variable"
_SARIF_RULE: Dict[str, Any] = {
    "id": _SARIF_RULE_ID,
    "name": "UndeclaredVariable",
    "shortDescription": {
        "text": "An environment variable is read in source but not declared in the schema."
    },
    "helpUri": "https://docs.envshield.dev",
    "defaultConfiguration": {"level": "error"},
}


def _sarif_uri(file_path: str) -> str:
    """SARIF artifact locations are URIs -- always forward-slash, regardless of host OS."""
    return file_path.replace(os.sep, "/")


def to_sarif(
    reports: List[Tuple[str, DependencyChangeReport]],
    tool_version: str,
    errors: Optional[List[Tuple[Optional[str], str]]] = None,
) -> Dict[str, Any]:
    """
    A SARIF 2.1.0 log for one or more services' undeclared-variable
    reports -- a pure presentation mapping over already-computed
    DependencyChangeReport data (`reports`), the same "domain computes,
    presentation renders" split every other JSON/table output in this
    codebase already follows. No new discovery/classification logic lives
    here.

    Only 'missing_declaration' changes become SARIF results -- an already-
    declared new usage isn't an actionable finding a CI/PR gate should
    flag (the existing '--json' output already reports every change, for
    anyone who wants the full audit trail; SARIF is specifically for
    actionable annotations, and a result for every already-correct usage
    would bury the one actionable finding in noise).

    `errors` records services this invocation could not evaluate at all
    (e.g. a missing schema, or a project-level error with no single
    service -- pass `None` as that entry's service name) as SARIF's own
    "execution not fully successful" mechanism (`invocations`), never as a
    fabricated result -- the same distinction `scan_result`'s `complete`
    flag draws for secret scanning (found-nothing vs. couldn't-check),
    expressed through SARIF's own standard vocabulary instead of a bespoke
    field invented for this command alone.
    """
    results: List[Dict[str, Any]] = []
    for service_name, report in reports:
        for change in report.changes:
            if change.category != "missing_declaration":
                continue
            results.append(
                {
                    "ruleId": _SARIF_RULE_ID,
                    "level": "error",
                    "message": {
                        "text": (
                            f"'{change.variable}' is read in source but not "
                            f"declared in the '{service_name}' schema."
                        )
                    },
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": _sarif_uri(change.file_path)
                                },
                                "region": {"startLine": change.line},
                            }
                        }
                    ],
                    "properties": {
                        "service": service_name,
                        "variable": change.variable,
                        "language": change.language,
                        "access_type": change.access_type,
                    },
                }
            )

    run: Dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "envshield",
                "informationUri": "https://docs.envshield.dev",
                "version": tool_version,
                "rules": [_SARIF_RULE],
            }
        },
        "results": results,
    }
    if errors:
        run["invocations"] = [
            {
                "executionSuccessful": False,
                "toolExecutionNotifications": [
                    {
                        "level": "error",
                        "message": {
                            "text": (
                                message
                                if service_name is None
                                else f"Could not evaluate service '{service_name}': {message}"
                            )
                        },
                    }
                    for service_name, message in errors
                ],
            }
        ]

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [run],
    }
