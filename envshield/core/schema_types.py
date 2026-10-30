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
from typing import Any, Dict
from urllib.parse import urlparse

KNOWN_TYPES = {"string", "int", "float", "bool", "port", "url", "email", "enum"}

# Every key a field table may carry (see docs/architecture/evaluator-
# decisions.md D-2). `services` is schema_scope.SCOPE_KEY, spelled out here
# to keep this module import-free.
FIELD_KEYS = frozenset(
    {
        "type",
        "enum",
        "pattern",
        "defaultValue",
        "requiredIf",
        "requiredIn",
        "required",
        "secret",
        "description",
        "services",
        "source",
    }
)
_REQUIREDIF_KEYS = frozenset({"var", "equals"})
# The built-in environment; others are declared in envshield.yml (plan §7).
LOCAL = "local"

# What 'init'/'import' write as a variable's description until a person
# writes one. Never copied into a generated template (#45, BL-177).
PLACEHOLDER_DESCRIPTION = "TODO: Add description."
ENVIRONMENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_REQUIREDNESS_KEYS = ("required", "requiredIn", "requiredIf")
# Near-misses worth naming in the error -- `default` in particular, since
# nothing else would tell a user their default was silently ignored.
_KEY_HINTS = {"default": "defaultValue", "required_if": "requiredIf"}

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


def field_problems(name: str, field_schema: Any) -> list[str]:
    """
    Structural problems with one schema entry, as human-readable strings
    naming only keys and types -- never a value (a mistyped `required =
    "..."` could hold anything). Empty when the entry is valid. Pure, so
    the live loader and tests share one definition of "valid".
    """
    if not is_safe_variable_name(name):
        return [
            f"{name!r} is not a valid variable name (must match "
            "^[A-Za-z_][A-Za-z0-9_]*$)"
        ]
    if not isinstance(field_schema, dict):
        return [f"'{name}' must be a table ([{name}]), not a bare value"]

    problems = []
    for key in sorted(set(field_schema) - FIELD_KEYS):
        hint = _KEY_HINTS.get(key)
        problems.append(
            f"'{name}' has unknown key '{key}'"
            + (f" (did you mean '{hint}'?)" if hint else "")
        )
    declared_type = field_schema.get("type")
    if declared_type is not None and not isinstance(declared_type, str):
        problems.append(f"'{name}'.type must be a string")
    elif declared_type is not None and declared_type not in KNOWN_TYPES:
        problems.append(
            f"'{name}' has unknown type {declared_type!r} (one of: "
            f"{', '.join(sorted(KNOWN_TYPES))})"
        )
    for key in ("required", "secret"):
        if key in field_schema and not isinstance(field_schema[key], bool):
            problems.append(f"'{name}'.{key} must be true or false")
    if "source" in field_schema and field_schema["source"] not in (
        "local",
        "secret-manager",
    ):
        problems.append(f'\'{name}\'.source must be "local" or "secret-manager"')
    if "enum" in field_schema and not isinstance(field_schema["enum"], list):
        problems.append(f"'{name}'.enum must be a list")
    condition = field_schema.get("requiredIf")
    if condition is not None:
        if not isinstance(condition, dict):
            problems.append(
                f"'{name}'.requiredIf must be a table like "
                '{ var = "OTHER", equals = "value" }'
            )
        else:
            for key in sorted(set(condition) - _REQUIREDIF_KEYS):
                problems.append(f"'{name}'.requiredIf has unknown key '{key}'")
    environments = field_schema.get("requiredIn")
    if "requiredIn" in field_schema and not (
        isinstance(environments, list)
        and all(
            isinstance(e, str) and ENVIRONMENT_NAME_RE.match(e) for e in environments
        )
    ):
        problems.append(
            f"'{name}'.requiredIn must be a list of environment names, like "
            '["staging", "production"]'
        )
    used = [key for key in _REQUIREDNESS_KEYS if key in field_schema]
    if len(used) > 1:
        problems.append(
            f"'{name}' sets {' and '.join(repr(k) for k in used)} -- use one: "
            "each one alone says when it's required"
        )
    return problems


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


def secret_default_conflict(field_schema: dict[str, Any]) -> bool:
    """
    True if a field is declared `secret` and also carries a real
    (non-empty) `defaultValue`. This combination is never valid: a
    defaultValue is written verbatim into every place a schema's defaults
    are surfaced -- '.env.example', generated Python, generated
    TypeScript, `explain`'s field description, `check`'s "missing/blank"
    report -- and `secret` exists specifically to keep a value out of
    exactly those committed, generated, or displayed surfaces. An
    empty-string default ("this secret is optional, with no value if
    unset") is not a conflict; it carries nothing to leak.

    This mirrors the invariant `importer.py`'s schema generation already
    enforces unconditionally when *writing* a new schema (a variable
    classified secret never gets a defaultValue in the first place) --
    this function is the corresponding check for code that *reads* a
    schema that may not have gone through that writer.
    """
    if not field_schema.get("secret"):
        return False
    return str(field_schema.get("defaultValue", "")) != ""


# A key-value connection string's password field (ADO.NET, ODBC, libpq):
# "Server=db;User Id=app;Password=hunter2", "host=db password=hunter2".
_DSN_PASSWORD_RE = re.compile(r"(?i)(?:^|[;\s])(?:password|pwd)\s*=\s*[^;\s]")


def value_embeds_credential(value: Any) -> bool:
    """
    Whether a value carries a credential whatever its variable's name
    (BL-208): a URL with a password in its userinfo (scheme://user:pass@host,
    scheme://:pass@host), or a connection string with a password field.
    A URL with only a user name, or none, doesn't count. Used by the
    importer (never write it as a default) and by schema loading (reject
    it as one, BL-209).
    """
    if not isinstance(value, str) or not value:
        return False
    if "@" in value:
        try:
            parsed = urlparse(value)
            if parsed.scheme and parsed.netloc and parsed.password:
                return True
        except ValueError:
            # A malformed URL (an unbalanced IPv6 bracket, a bad port) is
            # judged by its text below.
            pass
        authority = value.split("://", 1)[-1].split("/", 1)[0]
        userinfo = authority.rpartition("@")[0]
        if ":" in userinfo and userinfo.split(":", 1)[1]:
            return True
    return bool(_DSN_PASSWORD_RE.search(value))


def default_holds_credential(field_schema: Dict[str, Any]) -> bool:
    """
    True if a field's `defaultValue` carries a credential (BL-209), whatever
    `secret` says: a default is written verbatim into .env.example and
    generated code, so a password in it is committed with them.
    """
    if not isinstance(field_schema, dict):
        return False
    return value_embeds_credential(field_schema.get("defaultValue"))


def default_must_not_render(field_schema: Dict[str, Any]) -> bool:
    """
    A field whose default may never be written or shown anywhere: a secret
    with a default (BL-001) or a default that holds a credential (BL-209).
    Schema loading rejects both; renderers check this as a backstop.
    """
    return secret_default_conflict(field_schema) or default_holds_credential(
        field_schema
    )


def presence_rule(field_schema: Dict[str, Any], env: str = LOCAL) -> str:
    """
    When a field must be explicitly present (non-blank) in a checked file
    in environment `env`: 'always', 'conditional' (its requiredIf
    decides), or 'never' (plan §7, docs/architecture/evaluator-decisions.md
    D-2).

    An explicit boolean `required` decides outright, and `requiredIn`
    decides by whether `env` is listed. Otherwise `requiredIf` makes it
    conditional, and anything else (with or without a defaultValue) is
    always required. A defaultValue never changes requiredness: it's what
    'setup' fills in, not proof a value exists (D-1).
    """
    required = field_schema.get("required")
    if required is True:
        return "always"
    if required is False:
        return "never"
    if "requiredIn" in field_schema:
        return "always" if env in (field_schema["requiredIn"] or []) else "never"
    if field_schema.get("requiredIf"):
        return "conditional"
    return "always"


def requiredness_label(field_schema: Dict[str, Any], env: str = LOCAL) -> str:
    """presence_rule, as the contract vocabulary 'explain' and reports use."""
    return {"always": "required", "conditional": "conditional", "never": "optional"}[
        presence_rule(field_schema, env)
    ]


def _condition_holds(
    field_schema: Dict[str, Any], local_values: Dict[str, str]
) -> bool:
    condition = field_schema.get("requiredIf")
    other_var = condition.get("var") if isinstance(condition, dict) else None
    if not other_var:
        return True
    expected = str(condition.get("equals", "true"))
    return local_values.get(other_var) == expected


def is_required_now(
    field_schema: Dict[str, Any], local_values: Dict[str, str], env: str = LOCAL
) -> bool:
    """
    Whether 'setup' must ask for this field right now, given its
    `requiredIf` condition (if any) evaluated against the project's other
    local values.

    A field with a `defaultValue` never needs asking -- 'setup' fills the
    default in instead. That's a prompting decision only: whether the file
    must end up containing the field is should_be_present.
    """
    if "defaultValue" in field_schema:
        return False
    rule = presence_rule(field_schema, env)
    if rule == "never":
        return False
    if rule == "conditional":
        return _condition_holds(field_schema, local_values)
    return True


def requiredif_condition_text(
    field_schema: dict[str, Any], schema: dict[str, Any]
) -> str | None:
    """
    Secret-safe, human-readable rendering of a field's `requiredIf`
    condition -- e.g. 'PAYMENTS_ENABLED = "true"'. If the triggering
    variable is itself declared `secret` in `schema`, the comparison
    literal is withheld ('STRIPE_TOKEN is set') instead: it's schema-
    authored, but nothing stops it from coinciding with (or hinting at) a
    real secret value, so it's treated the same as a value itself would
    be. Returns None if the field has no `requiredIf`, or the condition is
    malformed (missing 'var').

    Shared by 'setup's prompt-time explanation and 'schema sync's
    '.env.example' annotation -- the two places this condition is
    surfaced to a human outside of 'explain'.
    """
    condition = field_schema.get("requiredIf")
    if not condition:
        return None
    var = condition.get("var")
    if not var:
        return None
    if schema.get(var, {}).get("secret"):
        return f"{var} is set"
    equals = str(condition.get("equals", "true"))
    return f'{var} = "{equals}"'


def should_be_present(
    field_schema: Dict[str, Any], local_values: Dict[str, str], env: str = LOCAL
) -> bool:
    """
    Whether a field must have an explicit, non-blank value in the target
    file -- what 'check'/'doctor' enforce. Broader than is_required_now
    (setup's prompt-or-fill decision): a defaultValue is a starting value
    'setup' writes automatically, not license for the file's own copy to
    stay silently absent or blank -- nothing guarantees whatever reads this
    file actually falls back the same way, or falls back at all. See
    presence_rule.
    """
    rule = presence_rule(field_schema, env)
    if rule == "conditional":
        return _condition_holds(field_schema, local_values)
    return rule == "always"
