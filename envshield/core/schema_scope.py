# envshield/core/schema_scope.py
"""
Service scope projection: one hand-maintained system schema, many logical
services.

A variable may declare `services` -- the reserved attribute naming which
registered services receive it:

    [DATABASE_URL]
    secret = true
    services = ["api", "worker"]        # grant only

    [PORT]
    type = "port"
    [PORT.services.api]                 # grant + per-service override
    defaultValue = "8000"
    [PORT.services.worker]
    defaultValue = "9000"

`project` is the single place this is interpreted -- the live loader
(config_manager.load_schema_view) and the revision loader
(schema_snapshot.load_schema_view_for_diff) both call it, differing only
in where the registered-service set comes from. It validates the whole
system schema (every user, not just the one requested), so a defect that
only affects one service fails every service's load instead of hiding
until someone happens to run that one service's command.

Pure: no I/O, no envshield.yml -- the same inputs always give the same
view (and never mutate the input).
"""

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .exceptions import (
    FileContractConflictError,
    SchemaScopeError,
    SecretDefaultConflictError,
)

SCOPE_KEY = "services"
OVERRIDABLE_KEYS = frozenset({"defaultValue", "description"})


@dataclass(frozen=True)
class ServiceSchemaView:
    """
    One service's view of a (possibly shared) schema.

    `fields` is exactly the shape load_schema has always returned -- the
    service's effective schema, `services` stripped, per-service overrides
    applied. `system` is every variable the schema defines (`services`
    stripped, no overrides), so a caller can tell "defined but not granted
    to this service" apart from "not defined at all". `grants[var]` is the
    set of services a variable is granted to, or None for an unscoped
    (global) variable.
    """

    service: str
    schema_path: str
    fields: Dict[str, Any]
    system: Dict[str, Any]
    grants: Dict[str, Optional[FrozenSet[str]]]
    users: Tuple[str, ...]

    @property
    def shared(self) -> bool:
        return len(self.users) > 1

    @property
    def scoped(self) -> bool:
        return any(g is not None for g in self.grants.values())

    def status(self, var: str) -> str:
        """'in_scope', 'out_of_scope' (defined, not granted), or 'undefined'."""
        if var in self.fields:
            return "in_scope"
        if var in self.system:
            return "out_of_scope"
        return "undefined"

    def granted_to(self, var: str) -> Optional[FrozenSet[str]]:
        """Every service `var` reaches, or None if it isn't defined at all."""
        if var not in self.system:
            return None
        grant = self.grants.get(var)
        return frozenset(self.users) if grant is None else grant


def _parse_scope(
    key: str, raw: Any, schema_path: str
) -> Tuple[FrozenSet[str], Dict[str, Dict[str, Any]]]:
    """Returns (granted services, per-service overrides) for one `services` value."""
    if isinstance(raw, list):
        if not all(isinstance(name, str) for name in raw):
            raise SchemaScopeError(
                schema_path, f"'{key}'.services must be a list of service names."
            )
        if len(set(raw)) != len(raw):
            raise SchemaScopeError(
                schema_path, f"'{key}'.services lists the same service twice."
            )
        names, overrides = raw, {}
    elif isinstance(raw, dict):
        for name, override in raw.items():
            if not isinstance(override, dict):
                raise SchemaScopeError(
                    schema_path,
                    f"'{key}'.services.{name} must be a table (e.g. "
                    f"[{key}.services.{name}] with defaultValue/description), "
                    "not a bare value.",
                )
            disallowed = sorted(set(override) - OVERRIDABLE_KEYS)
            if disallowed:
                raise SchemaScopeError(
                    schema_path,
                    f"'{key}'.services.{name} sets {', '.join(disallowed)}; only "
                    "defaultValue and description can vary by service -- type, "
                    "secret, enum, pattern, and requiredIf apply to every service.",
                )
        names, overrides = list(raw), raw
    else:
        raise SchemaScopeError(
            schema_path,
            f"'{key}'.services must be a list of service names or a table of "
            "per-service overrides.",
        )
    if not names:
        raise SchemaScopeError(
            schema_path,
            f"'{key}'.services is empty -- it's granted to no service. List the "
            "services that receive it, or remove the variable.",
        )
    return frozenset(names), overrides


def _unknown_service_message(
    key: str, name: str, users: Sequence[str], registered: Iterable[str]
) -> str:
    using = ", ".join(sorted(users))
    if name in set(registered):
        return (
            f"'{key}'.services names '{name}', which is registered but uses a "
            f"different schema file. Services using this schema: {using}."
        )
    return (
        f"'{key}'.services names '{name}', which isn't a registered service. "
        f"Services using this schema: {using}. If you removed or renamed "
        f"'{name}', update the services entries in this schema."
    )


def project(
    merged: Dict[str, Any],
    schema_path: str,
    service: str,
    users: Sequence[str],
    registered: Iterable[str] = (),
) -> ServiceSchemaView:
    """
    Validates `merged` (a fully extends-merged schema) against `users` (the
    registered services whose `schema` is this same file) and returns
    `service`'s view of it. `registered` (every registered service name) is
    only used to word an unknown-service error.

    Raises SchemaScopeError / SecretDefaultConflictError -- see
    schema_scope's module docstring for the rules.
    """
    if service not in users:
        raise SchemaScopeError(
            schema_path, f"service '{service}' is not registered against this schema."
        )
    user_set = frozenset(users)
    registered = list(registered)
    shared = len(user_set) > 1

    system: Dict[str, Any] = {}
    grants: Dict[str, Optional[FrozenSet[str]]] = {}
    overrides: Dict[str, Dict[str, Dict[str, Any]]] = {}
    unscoped_secrets: List[str] = []
    secret_override_defaults: List[str] = []

    for key, details in merged.items():
        if not isinstance(details, dict) or SCOPE_KEY not in details:
            system[key] = details
            grants[key] = None
            if shared and isinstance(details, dict) and details.get("secret"):
                unscoped_secrets.append(key)
            continue

        granted, key_overrides = _parse_scope(key, details[SCOPE_KEY], schema_path)
        unknown = sorted(granted - user_set)
        if unknown:
            raise SchemaScopeError(
                schema_path,
                _unknown_service_message(key, unknown[0], users, registered),
            )
        system[key] = {k: v for k, v in details.items() if k != SCOPE_KEY}
        grants[key] = granted
        overrides[key] = key_overrides
        if details.get("secret"):
            for name, override in sorted(key_overrides.items()):
                if str(override.get("defaultValue", "")) != "":
                    secret_override_defaults.append(f"{key} (services.{name})")

    if unscoped_secrets:
        raise SchemaScopeError(
            schema_path,
            f"secret variable(s) {', '.join(repr(k) for k in unscoped_secrets)} "
            f"have no 'services' list, and this schema is shared by "
            f"{', '.join(sorted(user_set))}. Add services = [...] naming exactly "
            "which services may receive each one -- a shared schema never grants "
            "a secret to every service implicitly.",
        )
    if secret_override_defaults:
        raise SecretDefaultConflictError(schema_path, secret_override_defaults)

    def reach(var: str) -> FrozenSet[str]:
        grant = grants[var]
        return user_set if grant is None else grant

    for key, details in system.items():
        if not isinstance(details, dict):
            continue
        condition = details.get("requiredIf")
        dependency = condition.get("var") if isinstance(condition, dict) else None
        if not dependency or dependency not in system:
            continue  # undefined dependency: unchanged legacy semantics
        missing = sorted(reach(key) - reach(dependency))
        if missing:
            raise SchemaScopeError(
                schema_path,
                f"'{key}' is required when '{dependency}' is set, but "
                f"'{dependency}' is not available to {', '.join(missing)} and "
                f"'{key}' is. Grant '{dependency}' to {', '.join(missing)}, or "
                f"remove {', '.join(missing)} from '{key}'s services.",
            )

    fields: Dict[str, Any] = {}
    for key, details in system.items():
        if service not in reach(key):
            continue
        override = overrides.get(key, {}).get(service)
        fields[key] = {**details, **override} if override else details

    return ServiceSchemaView(
        service=service,
        schema_path=schema_path,
        fields=fields,
        system=system,
        grants=grants,
        users=tuple(sorted(user_set)),
    )


def union_fields(views: Sequence[ServiceSchemaView], file_path: str) -> Dict[str, Any]:
    """
    The physical-file contract: the union of the effective fields of every
    service whose env file is the same physical `file_path`. Views are taken
    in service-name order, so envshield.yml order never changes the result.

    A variable more than one service receives must have the same effective
    definition in each -- e.g. two different per-service defaultValues
    raise FileContractConflictError instead of one being picked. The one
    exception is `description`, service-level metadata: when sharing
    services' descriptions differ, the union carries none rather than
    choosing one. File-level only: it grants no service anything; each
    service's own view is unchanged.
    """
    fields: Dict[str, Any] = {}
    owner: Dict[str, str] = {}
    for view in sorted(views, key=lambda v: v.service):
        for key, details in view.fields.items():
            if key not in fields:
                fields[key] = details
                owner[key] = view.service
                continue
            first = fields[key]
            if not (isinstance(first, dict) and isinstance(details, dict)):
                if first != details:
                    raise FileContractConflictError(
                        file_path, key, owner[key], view.service, ["definition"]
                    )
                continue
            differing = sorted(
                k
                for k in set(first) | set(details)
                if k != "description" and first.get(k) != details.get(k)
            )
            if differing:
                raise FileContractConflictError(
                    file_path, key, owner[key], view.service, differing
                )
            if first.get("description") != details.get("description"):
                fields[key] = {k: v for k, v in first.items() if k != "description"}
    return fields
