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


def validate_value(value: str, field_schema: dict[str, Any]) -> str | None:
    """
    Checks `value` against a field's declared type, enum, and/or pattern
    constraints. Returns None if valid, or a human-readable reason if not.
    """
    field_type = resolve_field_type(field_schema)

    if field_type == "enum":
        allowed = enum_values(field_schema)
        if value not in allowed:
            return f"must be one of: {', '.join(allowed)} (got {value!r})"
    elif field_type == "int":
        if not re.fullmatch(r"-?\d+", value):
            return f"must be an integer (got {value!r})"
    elif field_type == "float":
        try:
            float(value)
        except ValueError:
            return f"must be a number (got {value!r})"
    elif field_type == "bool":
        if value.lower() not in _BOOL_VALUES:
            return f"must be 'true' or 'false' (got {value!r})"
    elif field_type == "port":
        if not re.fullmatch(r"\d+", value) or not (1 <= int(value) <= 65535):
            return f"must be a port number from 1-65535 (got {value!r})"
    elif field_type == "url":
        parsed = urlparse(value)
        if not (parsed.scheme and parsed.netloc):
            return f"must be a valid URL (got {value!r})"
    elif field_type == "email":
        if not _EMAIL_RE.match(value):
            return f"must be a valid email address (got {value!r})"
    # "string" (the default): no shape check beyond 'pattern' below.

    pattern = field_schema.get("pattern")
    if pattern and not re.search(pattern, value):
        return f"must match pattern {pattern!r} (got {value!r})"

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
