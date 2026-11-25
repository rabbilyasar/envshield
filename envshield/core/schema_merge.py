# envshield/core/schema_merge.py
"""
'envshield schema merge' (V6-1, #26): every registered service's schema,
merged into one project contract.

- merge(): each service's effective schema (its projection, with `extends`
  resolved by the loader) to one contract. A variable every holder defines
  the same way is one table; a differing description or non-secret default
  becomes a per-service override; any other difference is a conflict, and
  nothing is produced. A secret is always granted to exactly the services
  that had it (never broadened); only a non-secret every service had is
  unscoped.
- render(): the contract as TOML, checked to parse back to exactly what was
  merged.
- proposed_config(): envshield.yml with a top-level `schema:` and every
  service on it, each with the `dir` it already had.
- compare(): the verdict-equivalence gate: every service's verdicts in
  every environment, under the current layout and under the proposed one
  (config_manager.config_override, so nothing is written to judge it).
  Each service's code is read once for both layouts when it owns the same
  files under both (evaluator.reuse_code_references).

Reads and returns schema attributes only; never a value from a local file.
"""

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import toml

from envshield.config import manager as config_manager
from envshield.core import evaluator, schema_types
from envshield.core.exceptions import EnvShieldException

# The keys a per-service override may set (shared-system-schema.md §2).
OVERRIDE_KEYS = ("description", "defaultValue")

HEADER = (
    "# The project contract, merged by 'envshield schema merge' from:\n"
    "{sources}"
    "# Comments in those files are not carried over.\n\n"
)


@dataclass
class Conflict:
    """One variable defined differently by two services: never merged."""

    variable: str
    attribute: str
    values: Dict[str, Any]  # service -> its value (schema text, never a .env value)

    def text(self) -> str:
        shown = "; ".join(f"{s}: {v!r}" for s, v in sorted(self.values.items()))
        return f"{self.variable}: '{self.attribute}' differs ({shown})"


@dataclass
class MergeResult:
    contract: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    conflicts: List[Conflict] = field(default_factory=list)


def _signature(fields: Dict[str, Any], environments: List[str]) -> Dict[str, Any]:
    """What a field means for checking, attribute by attribute: two fields
    with equal signatures are judged the same way."""
    presence = tuple(schema_types.presence_rule(fields, env) for env in environments)
    sig: Dict[str, Any] = {
        "type": schema_types.resolve_field_type(fields),
        "secret": bool(fields.get("secret", False)),
        "enum": sorted(schema_types.enum_values(fields)),
        "pattern": fields.get("pattern"),
        "required": presence,
        "requiredIf": fields.get("requiredIf") if "conditional" in presence else None,
    }
    for key, value in fields.items():
        if key in OVERRIDE_KEYS or key in ("type", "secret", "enum", "pattern"):
            continue
        if key in ("required", "requiredIn", "requiredIf"):
            continue
        sig[key] = value
    return sig


def _default(fields: Dict[str, Any]) -> Optional[str]:
    if "defaultValue" not in fields:
        return None
    return schema_types.normalize_default_value(
        fields["defaultValue"], schema_types.resolve_field_type(fields)
    )


def merge(
    per_service: Dict[str, Dict[str, Dict[str, Any]]], environments: List[str]
) -> MergeResult:
    """
    `per_service`: service -> its effective schema (load_schema). Returns
    the merged contract, or the conflicts (then the contract is empty).
    """
    result = MergeResult()
    services = sorted(per_service)
    order: List[str] = []
    for service in services:
        for name in per_service[service]:
            if name not in order:
                order.append(name)

    for name in order:
        holders = [s for s in services if name in per_service[s]]
        tables = {s: per_service[s][name] for s in holders}
        first = tables[holders[0]]
        signatures = {s: _signature(t, environments) for s, t in tables.items()}
        base_sig = signatures[holders[0]]
        conflicted = False
        for attribute in sorted({k for sig in signatures.values() for k in sig}):
            values = {s: sig.get(attribute) for s, sig in signatures.items()}
            if len({repr(v) for v in values.values()}) > 1:
                result.conflicts.append(Conflict(name, attribute, values))
                conflicted = True
        if conflicted:
            continue

        table = {
            k: copy.deepcopy(v) for k, v in first.items() if k not in OVERRIDE_KEYS
        }
        secret = base_sig["secret"]
        descriptions = {s: t.get("description") for s, t in tables.items()}
        defaults = {s: _default(t) for s, t in tables.items()}
        overrides: Dict[str, Dict[str, Any]] = {s: {} for s in holders}
        if len(set(descriptions.values())) == 1:
            if descriptions[holders[0]] is not None:
                table["description"] = descriptions[holders[0]]
        else:
            for s, text in descriptions.items():
                if text is not None:
                    overrides[s]["description"] = text
        if not secret:
            if len(set(defaults.values())) == 1:
                if "defaultValue" in first:
                    table["defaultValue"] = first["defaultValue"]
            else:
                for s, t in tables.items():
                    if "defaultValue" in t:
                        overrides[s]["defaultValue"] = t["defaultValue"]

        has_overrides = any(overrides.values())
        if len(services) > 1:
            if has_overrides:
                table["services"] = overrides
            elif secret or holders != services:
                # A secret is granted to exactly its holders, never to all
                # by default; a non-secret only some services had likewise.
                table["services"] = list(holders)
        result.contract[name] = table

    if result.conflicts:
        result.contract = {}
    return result


def render(contract: Dict[str, Dict[str, Any]], sources: List[str]) -> str:
    """The contract as TOML. Raises EnvShieldException if it wouldn't parse
    back to exactly `contract` (nothing is written then)."""
    header = HEADER.format(sources="".join(f"#   {p}\n" for p in sources))
    text = header + toml.dumps(contract)
    try:
        parsed = toml.loads(text)
    except Exception as e:  # toml raises several types
        raise EnvShieldException(f"The merged contract doesn't parse: {e}") from e
    if parsed != contract:
        raise EnvShieldException(
            "The merged contract doesn't parse back to what was merged; "
            "nothing was written."
        )
    return text


def proposed_config(config: Dict[str, Any], output: str) -> Dict[str, Any]:
    """envshield.yml with `schema: output` and every service on it, keeping
    the directory each one has now (a contract user needs an explicit dir)."""
    new = copy.deepcopy(config)
    new["schema"] = output.replace(os.sep, "/")
    services = new.get("services") or {}
    for name, entry in services.items():
        if not isinstance(entry, dict):
            raise EnvShieldException(
                f"Service '{name}' in {config_manager.CONFIG_FILE_NAME} isn't a "
                "mapping; fix it before merging."
            )
        directory = config_manager.get_service_dir(name)
        entry.pop("schema", None)
        entry["dir"] = directory.replace(os.sep, "/")
    # Keep the readable order: project_name, schema, the rest.
    ordered: Dict[str, Any] = {}
    for key in ("project_name", "schema"):
        if key in new:
            ordered[key] = new[key]
    for key, value in new.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


Verdicts = Dict[Tuple[str, str], Dict[str, str]]


def _verdicts(services: List[str], environments: List[str]) -> Verdicts:
    """(service, environment) -> variable (or '<service>') -> verdict."""
    out: Verdicts = {}
    for service in services:
        for env in environments:
            local = env == schema_types.LOCAL
            evaluation = evaluator.evaluate_service(service, code=local, env=env)
            judged = {
                name: verdict.verdict
                for name, (verdict, _) in evaluation.judged().items()
            }
            judged["<service>"] = evaluation.verdict().verdict
            out[(service, env)] = judged
    return out


def compare(
    services: List[str], environments: List[str], config: Dict[str, Any]
) -> List[str]:
    """
    The verdict-equivalence gate: every difference between the current
    layout and `config` (empty when equivalent). A proposed layout that
    fails to load is a difference too.
    """
    with evaluator.reuse_code_references():
        before = _verdicts(services, environments)
        try:
            with config_manager.config_override(config):
                for service in services:
                    config_manager.load_schema(service)
                after = _verdicts(services, environments)
        except EnvShieldException as e:
            return [f"the merged layout doesn't load: {e}"]
    differences = []
    for key in sorted(before):
        service, env = key
        old, new = before[key], after.get(key, {})
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                what = "the service" if name == "<service>" else name
                differences.append(
                    f"{service} ({env}): {what} is {old.get(name, 'not reported')} "
                    f"now, {new.get(name, 'not reported')} after the merge"
                )
    return differences
