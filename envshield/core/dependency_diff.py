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

from dataclasses import dataclass
from typing import Any, Dict, List, Set

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
