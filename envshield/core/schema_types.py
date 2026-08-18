# envshield/core/schema_types.py
"""
Shared type-resolution and value-validation logic for schema fields.

A field can now declare an explicit `type` ("string" | "int" | "float" |
"bool" | "port" | "url" | "email"), an `enum` (a list of allowed string
values -- always implies an enum type regardless of `type`), a `pattern`
(a regex the value must match, on top of whatever type check applies), and
a `requiredIf` condition (`{ var = "OTHER_VAR", equals = "some value" }`)
that makes the field required only when that condition holds, instead of
unconditionally whenever it has no `defaultValue`.

This module is the single source of truth for *validating a real value*
against those constraints (used by `check`, `doctor`, and `setup`). The
code generator has its own, deliberately looser type-resolution function
(`generator._effective_field_type`) that additionally infers int/bool from
a `defaultValue`'s shape when no explicit `type` is given, for backward
compatibility with schemas written before this module existed -- a field
with no explicit type genuinely has no runtime *constraint* here, but
codegen still benefits from guessing a friendlier type for a bare default
like `"3"` or `"true"`.
"""

import re
from typing import Any
from urllib.parse import urlparse

KNOWN_TYPES = {"string", "int", "float", "bool", "port", "url", "email", "enum"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BOOL_VALUES = {"true", "false"}
_SAFE_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_safe_variable_name(name: str) -> bool:
    """
    True if `name` is safe to emit as a bare variable name/assignment
    target in a generated Python module or dotenv file -- the standard
    POSIX/env-var identifier grammar, which is exactly Python's own
    identifier grammar restricted to ASCII. Unlike a value, a name that
    fails this can't be escaped into a safe form without changing its
    identity (callers match on it elsewhere), so it must be rejected
    rather than sanitized.
    """
    return bool(_SAFE_VARIABLE_NAME_RE.match(name))


def resolve_field_type(field_schema: dict[str, Any]) -> str:
    """Returns the effective type name for a field, defaulting to 'string' (unconstrained)."""
    if field_schema.get("enum"):
        return "enum"
    declared = field_schema.get("type")
    if declared:
        return declared
    return "string"


def enum_values(field_schema: dict[str, Any]) -> list[str]:
    return [str(v) for v in field_schema.get("enum", [])]


def normalize_default_value(default_value: Any, field_type: str) -> str:
    """
    Canonicalizes a defaultValue for semantic-equality comparison -- e.g.
    TOML's native integer 8080 and the string "8080" represent the exact
    same default and must compare equal, the same way validate_value's own
    per-type shape checks already treat both as the same shape (an int-
    typed value is just "matches -?\\d+", regardless of which Python type
    parsed it out of TOML). This is a comparison concern, not a literal-
    rendering one -- unlike generator.py's per-language literal renderers,
    it never needs to produce valid source syntax, only a form two
    different representations of the same default converge to.
    """
    value = str(default_value)
    if field_type in ("int", "port") and re.fullmatch(r"-?\d+", value):
        return str(int(value))
    if field_type == "float":
        try:
            return repr(float(value))
        except ValueError:
            return value
    if field_type == "bool":
        return str(value.lower() == "true")
    return value


def validate_value(value: str, field_schema: dict[str, Any]) -> str | None:
    """
    Checks `value` against a field's declared type, enum, and/or pattern
    constraints. Returns None if valid, or a human-readable reason if not.

    The reason never echoes `value` itself, in any form (not `repr()`, not
    `str()`, not a quoted excerpt) -- only what the *constraint* is. This
    isn't conditional on the field's `secret` flag: a validator has no
    reliable way to know, at the point an error is generated, whether the
    string it was just handed is safe to print (a `secret`-flagged field is
    the confirmed case, but an unflagged field can just as easily hold a
    value nobody intended to expose). Every caller -- `check`/`doctor`'s
    Rich and `--json` output, and `setup`'s retry-loop prompt -- inherits
    this from this one function; none of them should, or need to, re-decide
    it themselves.
    """
    field_type = resolve_field_type(field_schema)

    if field_type == "enum":
        allowed = enum_values(field_schema)
        if value not in allowed:
            return f"must be one of: {', '.join(allowed)}"
    elif field_type == "int":
        if not re.fullmatch(r"-?\d+", value):
            return "must be an integer"
    elif field_type == "float":
        try:
            float(value)
        except ValueError:
            return "must be a number"
    elif field_type == "bool":
        if value.lower() not in _BOOL_VALUES:
            return "must be 'true' or 'false'"
    elif field_type == "port":
        if not re.fullmatch(r"\d+", value) or not (1 <= int(value) <= 65535):
            return "must be a port number from 1-65535"
    elif field_type == "url":
        parsed = urlparse(value)
        if not (parsed.scheme and parsed.netloc):
            return "must be a valid URL"
    elif field_type == "email":
        if not _EMAIL_RE.match(value):
            return "must be a valid email address"
    # "string" (the default): no shape check beyond 'pattern' below.

    pattern = field_schema.get("pattern")
    if pattern and not re.search(pattern, value):
        return f"must match pattern {pattern!r}"

    return None


def is_required_now(field_schema: dict[str, Any], local_values: dict[str, str]) -> bool:
    """
    Whether a field is currently required, given its `requiredIf` condition
    (if any) evaluated against the project's other local values.

    A field with a `defaultValue` is never "required" -- consistent with
    every other required/missing check in EnvShield, a default is itself
    the fallback. A field with no `requiredIf` is required unconditionally,
    exactly as before this existed.
    """
    if "defaultValue" in field_schema:
        return False
    condition = field_schema.get("requiredIf")
    if not condition:
        return True
    other_var = condition.get("var")
    if not other_var:
        return True
    expected = str(condition.get("equals", "true"))
    return local_values.get(other_var) == expected


def should_be_present(
    field_schema: dict[str, Any], local_values: dict[str, str]
) -> bool:
    """
    Whether a field must have an explicit, non-blank value in the target
    file -- broader than is_required_now (used for setup's prompt-or-fill
    decision, which must stay exactly as it is). A defaultValue is a
    starting value 'setup' writes automatically, not license for the
    file's own copy to stay silently absent or blank: nothing guarantees
    whatever reads this file actually falls back the same way, or falls
    back at all -- that equivalence only holds once 'generate's output is
    the thing actually being read. A defaulted field must be present
    regardless of requiredIf; one with neither a default nor an active
    requiredIf is the only case that's genuinely optional right now.
    """
    return "defaultValue" in field_schema or is_required_now(field_schema, local_values)
