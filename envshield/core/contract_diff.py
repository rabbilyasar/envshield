# envshield/core/contract_diff.py
"""
Schema-to-schema Contract Diff: pure comparison and change classification.

This module knows nothing about Git, filesystem paths, envshield.yml, or
Rich -- it takes two already-resolved schema dicts (whatever
schema_snapshot.load_schema_for_diff or config_manager.load_schema handed
back) and produces a ContractDiff. Revision-awareness lives entirely in
schema_snapshot.py; this module is deliberately revision-agnostic so the
same diff_schemas() works regardless of where either side came from.

Classification standard: a change is "breaking" only when it can be proven,
from the two schemas alone, that some value/config valid under schema A
could become invalid under schema B -- never by assuming what a specific
real config's other values are. Where that can't be proven one way or the
other (e.g. one non-empty regex pattern changed to a different non-empty
pattern), the change is flagged as its own category rather than guessed at
as breaking or safe. See the categories below for the full taxonomy.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import schema_types

# Every change lands in exactly one of these:
#   breaking                    -- proven: a previously-valid config could now fail validation
#   non_breaking                -- proven widening/relaxation; safe
#   informational               -- removed variable, or a new conditionally-required one;
#                                   real impact depends on data this diff doesn't have
#   default_changed             -- observable behavior change; can't prove impact without
#                                   source/deployment context
#   security                    -- secret classification changed (tightened or weakened)
#   requires_review             -- genuinely undecidable from the two schemas alone
CATEGORIES = frozenset(
    {
        "breaking",
        "non_breaking",
        "informational",
        "default_changed",
        "security",
        "requires_review",
    }
)


@dataclass
class ContractChange:
    variable: str
    category: str
    description: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variable": self.variable,
            "category": self.category,
            "description": self.description,
            "detail": self.detail,
        }


# The default '--fail-on' set for the CLI: compatibility-breaking changes,
# a weakened (never a tightened -- see is_blocking_change) secret
# classification, and anything EnvShield can't prove one way or the other.
# default_changed/informational/non_breaking are deliberately excluded --
# none of them can be shown to invalidate an existing config.
DEFAULT_BLOCKING_CATEGORIES = frozenset({"breaking", "security", "requires_review"})


def is_blocking_change(
    change: ContractChange, blocking_categories: frozenset = DEFAULT_BLOCKING_CATEGORIES
) -> bool:
    """
    Whether `change` should fail a CI/PR gate under `blocking_categories`
    (see 'schema diff --fail-on'). 'security' is a single category
    covering both directions of a secret-classification change, but only
    a WEAKENED one (true -> false) blocks -- a tightened one (false ->
    true) is a strict improvement and must never block, even when
    'security' is in the blocking set. Every other category is a plain
    membership check.
    """
    if change.category not in blocking_categories:
        return False
    if change.category == "security":
        return change.detail.get("severity") == "weakened"
    return True


@dataclass
class ContractDiff:
    changes: List[ContractChange]

    @property
    def has_breaking_changes(self) -> bool:
        return any(c.category == "breaking" for c in self.changes)

    def has_blocking_changes(
        self, blocking_categories: frozenset = DEFAULT_BLOCKING_CATEGORIES
    ) -> bool:
        return any(is_blocking_change(c, blocking_categories) for c in self.changes)

    def to_dict(
        self, blocking_categories: frozenset = DEFAULT_BLOCKING_CATEGORIES
    ) -> Dict[str, Any]:
        return {
            "has_breaking_changes": self.has_breaking_changes,
            "has_blocking_changes": self.has_blocking_changes(blocking_categories),
            "changes": [
                {**c.to_dict(), "blocking": is_blocking_change(c, blocking_categories)}
                for c in self.changes
            ],
        }


def diff_schemas(schema_a: Dict[str, Any], schema_b: Dict[str, Any]) -> ContractDiff:
    """The one public entry point: compare two resolved schema dicts."""
    keys_a = set(schema_a.keys())
    keys_b = set(schema_b.keys())

    changes: List[ContractChange] = []

    for key in sorted(keys_b - keys_a):
        changes.append(_classify_added(key, schema_b[key]))

    for key in sorted(keys_a - keys_b):
        changes.append(
            ContractChange(
                variable=key,
                category="informational",
                description=(
                    f"'{key}' was removed from the contract. Schema-only diff "
                    "can't tell whether code still reads it -- treated as "
                    "informational, not breaking."
                ),
                detail={"removed": True},
            )
        )

    for key in sorted(keys_a & keys_b):
        changes.extend(_diff_field(key, schema_a[key], schema_b[key]))

    return ContractDiff(changes=changes)


def _requiredness_state(field_schema: Dict[str, Any]) -> str:
    """
    Three mutually exclusive states, matching schema_types.is_required_now
    exactly:

    'defaulted': has a defaultValue -- never required, regardless of
    requiredIf (is_required_now checks this first and returns False
    unconditionally).
    'conditional': no defaultValue, requiredIf present -- required only
    when that condition holds, which this module has no config data to
    evaluate.
    'unconditional': no defaultValue, no requiredIf -- always required.

    Distinguishing 'defaulted' from 'unconditional' (both were folded into
    one 'unconditional' state before this) matters because gaining or
    losing a bare defaultValue is itself a requiredness change -- see
    _classify_requiredness_transition -- not merely a default-value edit.
    """
    if "defaultValue" in field_schema:
        return "defaulted"
    if field_schema.get("requiredIf"):
        return "conditional"
    return "unconditional"


def _classify_added(key: str, field_schema: Dict[str, Any]) -> ContractChange:
    state = _requiredness_state(field_schema)
    if state == "defaulted":
        return ContractChange(
            variable=key,
            category="non_breaking",
            description=(
                f"'{key}' was added with a default value -- never required "
                "(is_required_now returns False unconditionally whenever a "
                "defaultValue is present), so every config valid before is "
                "still valid."
            ),
            detail={"added": True},
        )
    if state == "conditional":
        return ContractChange(
            variable=key,
            category="informational",
            description=(
                f"'{key}' was added, conditionally required via requiredIf. "
                "Whether an existing config is affected depends on that "
                "condition's referenced value, which schema-only diff can't see."
            ),
            detail={"added": True, "requiredIf": field_schema.get("requiredIf")},
        )
    return ContractChange(
        variable=key,
        category="breaking",
        description=(
            f"'{key}' was added and must always be present (no requiredIf, "
            "so nothing exempts an existing config from now needing it)."
        ),
        detail={"added": True},
    )


def _diff_field(key: str, a: Dict[str, Any], b: Dict[str, Any]) -> List[ContractChange]:
    changes: List[ContractChange] = []

    req_a, req_b = _requiredness_state(a), _requiredness_state(b)
    if req_a != req_b:
        changes.append(_classify_requiredness_transition(key, req_a, req_b))
    elif req_a == "conditional" and a.get("requiredIf") != b.get("requiredIf"):
        changes.append(
            ContractChange(
                variable=key,
                category="requires_review",
                description=(
                    f"'{key}' requiredIf condition changed. Whether this "
                    "breaks an existing config depends on a value schema-only "
                    "diff doesn't have."
                ),
                detail={"before": a.get("requiredIf"), "after": b.get("requiredIf")},
            )
        )

    type_a = schema_types.resolve_field_type(a)
    type_b = schema_types.resolve_field_type(b)
    if type_a != type_b:
        changes.append(_classify_type_change(key, type_a, type_b))

    pattern_a, pattern_b = a.get("pattern"), b.get("pattern")
    if pattern_a != pattern_b:
        changes.append(_classify_pattern_change(key, pattern_a, pattern_b))

    if type_a == "enum" and type_b == "enum":
        enum_a = set(schema_types.enum_values(a))
        enum_b = set(schema_types.enum_values(b))
        if enum_a != enum_b:
            changes.append(_classify_enum_change(key, enum_a, enum_b))

    default_a, default_b = a.get("defaultValue"), b.get("defaultValue")
    if _defaults_differ(default_a, default_b, type_a, type_b):
        changes.append(
            ContractChange(
                variable=key,
                category="default_changed",
                description=(
                    f"'{key}' default changed from {default_a!r} to "
                    f"{default_b!r}. This can't be proven to affect runtime "
                    "behavior without source or deployment context."
                ),
                detail={"before": default_a, "after": default_b},
            )
        )

    secret_a = bool(a.get("secret", False))
    secret_b = bool(b.get("secret", False))
    if secret_a != secret_b:
        changes.append(_classify_secret_change(key, secret_a, secret_b))

    return changes


def _defaults_differ(default_a: Any, default_b: Any, type_a: str, type_b: str) -> bool:
    """
    Only compares when both sides actually have a defaultValue -- gaining
    or losing one entirely (None on either side) is a requiredness change,
    owned by _requiredness_state's 'defaulted' state and
    _classify_requiredness_transition, not this function. Splitting it
    this way means a variable that gains a default is reported exactly
    once (as the non_breaking requiredness transition), not twice.

    Compares through schema_types.normalize_default_value rather than raw
    equality -- otherwise TOML's native int 8080 vs. the string "8080" (or
    'TRUE' vs. 'true' for a bool-typed default) would report a
    default_changed false positive purely from how TOML happened to
    represent an unchanged value, not from an actual change. Each side is
    normalized under its own resolved type, so a default that changed
    *because* the field's type also changed still compares correctly.
    """
    if default_a is None or default_b is None:
        return False
    return schema_types.normalize_default_value(
        default_a, type_a
    ) != schema_types.normalize_default_value(default_b, type_b)


# One entry per reachable (before, after) pair among the three
# _requiredness_state values -- same-state pairs never reach this function
# (the '_diff_field' call site only invokes it when req_a != req_b).
_REQUIREDNESS_TRANSITIONS: Dict[tuple, tuple] = {
    ("defaulted", "conditional"): (
        "requires_review",
        "'{key}' lost its default and gained a requiredIf condition -- "
        "whether this breaks an existing config depends on whether that "
        "condition currently holds, which schema-only diff can't see.",
    ),
    ("defaulted", "unconditional"): (
        "breaking",
        "'{key}' lost its default and is now always required -- a config "
        "that validly omitted it, relying on the default, is now invalid.",
    ),
    ("conditional", "defaulted"): (
        "non_breaking",
        "'{key}' gained a default -- every config valid before is still valid.",
    ),
    ("conditional", "unconditional"): (
        "breaking",
        "'{key}' requiredness changed from conditional to always required "
        "-- a config that validly omitted it while the old condition was "
        "unmet is now invalid.",
    ),
    ("unconditional", "defaulted"): (
        "non_breaking",
        "'{key}' gained a default -- every config valid before is still valid.",
    ),
    ("unconditional", "conditional"): (
        "non_breaking",
        "'{key}' requiredness relaxed from always-required to conditional "
        "-- every config that satisfied the stricter old rule still "
        "satisfies the relaxed one.",
    ),
}


def _classify_requiredness_transition(
    key: str, req_a: str, req_b: str
) -> ContractChange:
    category, description_template = _REQUIREDNESS_TRANSITIONS[(req_a, req_b)]
    return ContractChange(
        variable=key,
        category=category,
        description=description_template.format(key=key),
        detail={"before": req_a, "after": req_b},
    )


def _classify_type_change(key: str, type_a: str, type_b: str) -> ContractChange:
    if type_b == "string":
        return ContractChange(
            variable=key,
            category="non_breaking",
            description=f"'{key}' type widened from '{type_a}' to unconstrained 'string'.",
            detail={"before": type_a, "after": type_b},
        )
    if type_a == "string":
        return ContractChange(
            variable=key,
            category="breaking",
            description=f"'{key}' type narrowed from unconstrained 'string' to '{type_b}'.",
            detail={"before": type_a, "after": type_b},
        )
    return ContractChange(
        variable=key,
        category="breaking",
        description=(
            f"'{key}' type changed from '{type_a}' to '{type_b}' -- treated "
            "conservatively as breaking, since a value satisfying one "
            "specific type generally won't satisfy an unrelated one."
        ),
        detail={"before": type_a, "after": type_b},
    )


def _classify_pattern_change(
    key: str, pattern_a: Optional[str], pattern_b: Optional[str]
) -> ContractChange:
    if pattern_a and not pattern_b:
        return ContractChange(
            variable=key,
            category="non_breaking",
            description=f"'{key}' pattern constraint removed.",
            detail={"before": pattern_a, "after": None},
        )
    if not pattern_a and pattern_b:
        return ContractChange(
            variable=key,
            category="breaking",
            description=f"'{key}' pattern constraint added where none existed before.",
            detail={"before": None, "after": pattern_b},
        )
    return ContractChange(
        variable=key,
        category="requires_review",
        description=(
            f"'{key}' pattern changed. Whether the new pattern is a "
            "widening or a narrowing of the old one can't be proven from "
            "the two regexes alone."
        ),
        detail={"before": pattern_a, "after": pattern_b},
    )


def _classify_enum_change(key: str, enum_a: set, enum_b: set) -> ContractChange:
    removed = sorted(enum_a - enum_b)
    added = sorted(enum_b - enum_a)
    category = "breaking" if removed else "non_breaking"
    parts = []
    if removed:
        parts.append(f"removed: {removed}")
    if added:
        parts.append(f"added: {added}")
    return ContractChange(
        variable=key,
        category=category,
        description=f"'{key}' enum values changed ({'; '.join(parts)}).",
        detail={"removed": removed, "added": added},
    )


def _classify_secret_change(key: str, secret_a: bool, secret_b: bool) -> ContractChange:
    if secret_b and not secret_a:
        return ContractChange(
            variable=key,
            category="security",
            description=f"'{key}' secret classification tightened (false -> true).",
            detail={"before": False, "after": True, "severity": "tightened"},
        )
    return ContractChange(
        variable=key,
        category="security",
        description=(
            f"'{key}' secret classification WEAKENED (true -> false) -- it "
            "will no longer be masked in generated code, scan output, or "
            "validation-error messages."
        ),
        detail={"before": True, "after": False, "severity": "weakened"},
    )
