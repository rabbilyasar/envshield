# envshield/core/verdicts.py
"""
The five v5 verdicts (docs/architecture/v5-decisions.md, plan §10): one
per variable per source, judged from the source's typed value states
(parsers._base), then aggregated by precedence.

Invariant: VALID only ever comes from a Concrete value that was read and
validated. Every other state is COULD NOT VERIFY, INVALID or NOT REQUIRED.
A Verdict holds names and constraint descriptions only, never a value.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

from ..parsers._base import (
    UNSERVED_PIPE,
    Concrete,
    External,
    Forwarded,
    FromSecretManager,
    KeyPresent,
    Referenced,
    ValueState,
    ValueStates,
)
from . import schema_types

VALID = "valid"
INVALID = "invalid"
UNVERIFIED = "unverified"  # COULD NOT VERIFY
NOT_REQUIRED = "not_required"
ERROR = "error"

VERDICTS = (VALID, INVALID, UNVERIFIED, NOT_REQUIRED, ERROR)
# Precedence: ERROR > INVALID > COULD NOT VERIFY > VALID; NOT REQUIRED is neutral.
_RANK = {NOT_REQUIRED: -1, VALID: 0, UNVERIFIED: 1, INVALID: 2, ERROR: 3}

# Closed reason-code sets (plan §10).
REASONS = {
    INVALID: (
        "missing",
        "blank",
        "invalid_value",
        "file_not_found",
        "invalid_fallback",
        "extra",
        "out_of_scope",
        "undeclared_reference",
    ),
    UNVERIFIED: (
        "host_forward",
        "external_reference",
        "value_not_inspected",
        "secret_manager",
        "ambiguous_requirement",
    ),
    ERROR: (
        "contract_error",
        "topology_error",
        "source_parse_error",
        "source_unreadable",
        "secret_manager_error",
        "internal_error",
    ),
}

# The COULD NOT VERIFY reasons `check --accept-unverified` may accept (plan
# §10). Never `ambiguous_requirement` (the requirement itself is unknown),
# never an ERROR or INVALID reason, and no `all`.
ACCEPTABLE = (
    "host_forward",
    "external_reference",
    "value_not_inspected",
    "secret_manager",
)

REQUIRED, OPTIONAL, AMBIGUOUS = "required", "optional", "ambiguous"

# A required variable the secret manager didn't supply (V3-3); next_step
# keys on it.
NOT_SUPPLIED = (
    "not supplied by your secret manager: you may lack access, or it hasn't been added"
)

# The detail of a reference or encrypted value (BL-213); next_step keys on it.
_REFERENCE_SUFFIX = "EnvShield can't see the value it stands for"

# A required local variable that is absent but has a schema default.
HAS_DEFAULT = "required; has a default"

_OBJECT_LABEL = {
    "secretKeyRef": "Secret",
    "secretRef": "Secret",
    "configMapKeyRef": "ConfigMap",
    "configMapRef": "ConfigMap",
    "fieldRef": "Pod field",
    "resourceFieldRef": "container resource",
}


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: Optional[str] = (
        None  # a reason code from REASONS, None for VALID/NOT REQUIRED
    )
    detail: Optional[str] = None  # value-free explanation

    def __post_init__(self):
        assert self.verdict in VERDICTS
        assert self.reason is None or self.reason in REASONS[self.verdict]


# A high-confidence code read of a name the service isn't granted (D-7).
UNDECLARED = Verdict(
    INVALID,
    "undeclared_reference",
    "the code reads it, and the schema doesn't grant it to this service",
)


def worst(verdicts: Iterable[Verdict]) -> Verdict:
    """The highest-precedence verdict; NOT REQUIRED when there's nothing else."""
    best = Verdict(NOT_REQUIRED)
    for v in verdicts:
        if _RANK[v.verdict] > _RANK[best.verdict]:
            best = v
    return best


def parse_accepted(values: Iterable[str]) -> List[str]:
    """
    `--accept-unverified` values (each REASON[,REASON]) as a sorted list of
    distinct reasons. Raises ValueError, naming the reason, for anything
    outside ACCEPTABLE: a usage error.
    """
    accepted = set()
    for value in values:
        for reason in (r.strip() for r in value.split(",")):
            if not reason:
                continue
            if reason in ACCEPTABLE:
                accepted.add(reason)
            elif reason == "all":
                raise ValueError("there is no 'all': name each reason to accept")
            elif reason == "ambiguous_requirement":
                raise ValueError(
                    "'ambiguous_requirement' can't be accepted: whether the "
                    "variable is required is itself unknown"
                )
            elif reason in REASONS[ERROR] or reason in REASONS[INVALID]:
                raise ValueError(f"'{reason}' isn't a COULD NOT VERIFY reason")
            else:
                raise ValueError(f"unknown reason '{reason}'")
    if not accepted:
        raise ValueError("name at least one reason")
    return sorted(accepted)


def gate(
    verdict: str, unverified: Iterable[Mapping[str, Any]], accepted: Iterable[str]
) -> Dict[str, Any]:
    """
    Whether the pipeline may proceed (plan §10), given the invocation's
    verdict and its COULD NOT VERIFY findings. Passes on VALID, or on
    COULD NOT VERIFY when every finding's reason is accepted. The verdict
    itself never changes. `accepted` maps each accepted reason to the
    variables it covered.
    """
    accepted = set(accepted)
    covered: Dict[str, set] = {reason: set() for reason in sorted(accepted)}
    reasons = set()
    for finding in unverified:
        reasons.add(finding["reason_code"])
        if finding["reason_code"] in accepted and finding.get("variable"):
            covered[finding["reason_code"]].add(finding["variable"])
    # Fails closed: COULD NOT VERIFY with no finding to accept is a bug.
    passes = verdict in (VALID, NOT_REQUIRED) or (
        verdict == UNVERIFIED and bool(reasons) and reasons <= accepted
    )
    return {
        "result": "pass" if passes else "fail",
        "accepted": {reason: sorted(names) for reason, names in covered.items()},
    }


def _trigger_reason(trigger: str) -> str:
    return (
        f"required only if {trigger} has a certain value, and EnvShield "
        f"can't see {trigger}'s value here"
    )


def requirement(
    field_schema: Dict[str, Any], states: ValueStates, env: str = schema_types.LOCAL
) -> Optional[str]:
    """
    REQUIRED, OPTIONAL or AMBIGUOUS in this source. For a requiredIf, None
    when the trigger is absent from the source (no opinion; it compares as
    unset, so OPTIONAL by itself). A trigger that isn't a Concrete value --
    or that an unbound envFrom could supply -- is AMBIGUOUS.
    """
    rule = schema_types.presence_rule(field_schema, env)
    if rule == "always":
        return REQUIRED
    if rule == "never":
        return OPTIONAL
    condition = field_schema.get("requiredIf") or {}
    trigger = condition.get("var")
    if not trigger:
        return REQUIRED
    state = states.get(trigger)
    if state is None:
        return AMBIGUOUS if states.unbound else None
    if not isinstance(state, Concrete):
        return AMBIGUOUS
    expected = str(condition.get("equals", "true"))
    return REQUIRED if state.value == expected else OPTIONAL


def judge_variable(
    field_schema: Dict[str, Any],
    state: Optional[ValueState],
    need: Optional[str],
    *,
    unbound: Iterable[External] = (),
    missing_file: Optional[str] = None,
    trigger: Optional[str] = None,
    manifest: bool = False,
) -> Verdict:
    """One declared variable's verdict in one source."""
    need = need or OPTIONAL
    ambiguous = Verdict(UNVERIFIED, "ambiguous_requirement", _trigger_reason(trigger))

    if state is None:
        unbound = tuple(unbound)
        if need == OPTIONAL:
            return Verdict(NOT_REQUIRED)
        unserved = [u for u in unbound if u.kind == UNSERVED_PIPE]
        if unserved:
            return Verdict(
                UNVERIFIED,
                "value_not_inspected",
                f"'{unserved[0].ref}' is a mounted file that wasn't served; "
                "is your secret manager unlocked?",
            )
        if unbound:
            ref = unbound[0]
            return Verdict(
                UNVERIFIED,
                "external_reference",
                f"{_OBJECT_LABEL.get(ref.kind, ref.kind)} '{ref.ref}' is defined "
                "outside this manifest and may supply it",
            )
        if need == AMBIGUOUS:
            return ambiguous
        if missing_file:
            return Verdict(INVALID, "file_not_found", f"'{missing_file}' doesn't exist")
        if "defaultValue" in field_schema and not manifest:
            # Plan §7: a default is not evidence. Human output adds the hint.
            return Verdict(INVALID, "missing", HAS_DEFAULT)
        return Verdict(INVALID, "missing", "required")

    if isinstance(state, Concrete):
        error = schema_types.validate_value(state.value, field_schema)
        if state.value == "":
            if need == REQUIRED:
                return Verdict(INVALID, "blank", "required but empty")
            if error:
                return Verdict(INVALID, "invalid_value", error)
            return ambiguous if need == AMBIGUOUS else Verdict(VALID)
        if error:
            return Verdict(INVALID, "invalid_value", error)
        return Verdict(VALID)

    if isinstance(state, Forwarded):
        if state.operator in ("-", ":-"):
            fallback = state.fallback or ""
            if fallback == "" and need == REQUIRED:
                return Verdict(INVALID, "invalid_fallback", "the fallback is empty")
            error = schema_types.validate_value(fallback, field_schema)
            if error:
                return Verdict(INVALID, "invalid_fallback", f"the fallback: {error}")
            if state.operator == "-":
                detail = (
                    f"from the deploy host's {state.var}; with '-', an empty host "
                    "value reaches the container empty"
                )
            else:
                detail = (
                    f"from the deploy host's {state.var}; the fallback applies when "
                    "the host value is unset or empty"
                )
            return Verdict(UNVERIFIED, "host_forward", detail)
        if state.embedded:
            return Verdict(
                UNVERIFIED,
                "host_forward",
                f"the value includes the deploy host's {state.var}",
            )
        return Verdict(
            UNVERIFIED, "host_forward", f"from the deploy host's {state.var}"
        )

    if isinstance(state, External):
        return Verdict(
            UNVERIFIED,
            "external_reference",
            f"{_OBJECT_LABEL.get(state.kind, state.kind)} '{state.ref}' is defined "
            "outside this manifest",
        )

    if isinstance(state, Referenced):
        # Never VALID, and never INVALID either: the text isn't the value
        # the application gets (BL-213).
        return Verdict(
            UNVERIFIED, "external_reference", f"{state.form}; {_REFERENCE_SUFFIX}"
        )

    if isinstance(state, FromSecretManager):
        return _judge_secret_manager(field_schema, state, need)

    if isinstance(state, KeyPresent):
        return Verdict(
            UNVERIFIED,
            "value_not_inspected",
            f"'{state.ref}' is in this file; its value isn't inspected",
        )

    raise TypeError(f"unknown value state {type(state).__name__}")


def _judge_secret_manager(
    field_schema: Dict[str, Any], state: FromSecretManager, need: str
) -> Verdict:
    """A vault-supplied variable, from the secrets probe's status (V3-3)."""
    status, detail = state.status, state.detail
    if status == "valid":
        return Verdict(VALID)
    if status == "invalid":
        return Verdict(INVALID, "invalid_value", f"the secret manager's value {detail}")
    if status == "blank":
        if need == REQUIRED:
            return Verdict(INVALID, "blank", "your secret manager supplies it empty")
        if detail:
            return Verdict(
                INVALID, "invalid_value", f"the secret manager's value {detail}"
            )
        return Verdict(VALID)
    if status == "referenced":
        return Verdict(
            UNVERIFIED,
            "external_reference",
            f"{detail}, from your secret manager; {_REFERENCE_SUFFIX}",
        )
    if need == OPTIONAL:
        return Verdict(NOT_REQUIRED)
    if status == "missing":
        return Verdict(INVALID, "missing", NOT_SUPPLIED)
    if status == "failed":
        # The service-level ERROR names the failure; the variable is unchecked.
        return Verdict(
            UNVERIFIED,
            "secret_manager",
            f"from your secret manager, not checked ({detail})",
        )
    return Verdict(UNVERIFIED, "secret_manager", detail)


def judge_source(
    fields: Mapping[str, Dict[str, Any]],
    states: ValueStates,
    extra: Iterable[str] = (),
    out_of_scope: Iterable[str] = (),
    *,
    union: bool = False,
    manifest: bool = False,
    env: str = schema_types.LOCAL,
) -> Dict[str, Verdict]:
    """
    Every declared variable's verdict in one source, plus INVALID for each
    undeclared name (`extra`, or `out_of_scope` in a shared schema). In
    union mode a name absent from this source isn't judged here: the
    union decides (union_verdict).
    """
    out_of_scope = set(out_of_scope)
    missing_file = ", ".join(states.missing_files) or None
    result: Dict[str, Verdict] = {}
    for name, field_schema in fields.items():
        state = states.get(name)
        need = requirement(field_schema, states, env)
        if union and state is None:
            result[name] = Verdict(NOT_REQUIRED)
            continue
        condition = field_schema.get("requiredIf") or {}
        result[name] = judge_variable(
            field_schema,
            state,
            need,
            unbound=states.unbound,
            missing_file=missing_file,
            trigger=condition.get("var") if isinstance(condition, dict) else None,
            manifest=manifest,
        )
    for name in extra:
        if name in out_of_scope:
            result[name] = Verdict(
                INVALID, "out_of_scope", "declared, but not granted to this service"
            )
        else:
            result[name] = Verdict(INVALID, "extra", "not declared in the schema")
    return result


def union_requirement(field_schema: Dict[str, Any], needs: List[Optional[str]]) -> str:
    """Requiredness across union sources: their opinions must agree."""
    opinions = {n for n in needs if n is not None}
    if AMBIGUOUS in opinions or len(opinions) > 1:
        return AMBIGUOUS
    if opinions:
        return opinions.pop()
    rule = schema_types.presence_rule(field_schema)
    return OPTIONAL if rule in ("never", "conditional") else REQUIRED


def union_verdict(
    field_schema: Dict[str, Any],
    per_source: List[Verdict],
    need: str,
    unbound: Iterable[External] = (),
) -> Verdict:
    """
    One variable under completeness: union (D-4): VALID in any source makes
    it VALID; otherwise the worst present-value verdict; absent everywhere
    is judged against the union's requiredness.
    """
    if any(v.verdict == VALID for v in per_source):
        return Verdict(VALID)
    found = worst(per_source)
    if found.verdict != NOT_REQUIRED:
        return found
    unbound = tuple(unbound)
    if need != OPTIONAL and unbound:
        return judge_variable(field_schema, None, REQUIRED, unbound=unbound)
    if need == REQUIRED:
        return Verdict(INVALID, "missing", "required, and in no registered source")
    if need == AMBIGUOUS:
        trigger = (field_schema.get("requiredIf") or {}).get("var")
        return Verdict(UNVERIFIED, "ambiguous_requirement", _trigger_reason(trigger))
    return Verdict(NOT_REQUIRED)


def next_step(
    reason: Optional[str],
    *,
    variable: Optional[str] = None,
    service: str = "",
    file: Optional[str] = None,
    manifest: bool = False,
    secret: bool = False,
    has_default: bool = False,
    detail: Optional[str] = None,
    runtime_env: Optional[str] = None,
) -> Dict[str, str]:
    """
    What to do about one finding: {"by": "agent" | "human", "text": ...}.
    Deterministic, value-free, and never an interactive command. "agent"
    means a coding agent can make the fix from the repository alone;
    "human" means it needs a value, access or a decision only a person has.
    `runtime_env` names the deploy environment 'check --runtime' judged:
    the fix is there, never in a local file or 'setup' (plan §11, B3).
    """
    where = f" in {file}" if file else ""
    if runtime_env is not None:
        where = f" in the {runtime_env} deploy environment"
    var = variable or ""

    def human(text: str) -> Dict[str, str]:
        return {"by": "human", "text": text}

    def agent(text: str) -> Dict[str, str]:
        return {"by": "agent", "text": text}

    if reason == "missing" and detail == NOT_SUPPLIED:
        return human(f"Add {var} to your secret manager, or ask for access to it.")
    if reason == "secret_manager":
        return human(f"{var} comes {detail}.")
    if reason == "secret_manager_error":
        return human(f"Fix your secrets command: {detail}.")
    if reason in ("missing", "blank"):
        state = "missing" if reason == "missing" else "empty"
        if manifest:
            return human(
                f"{var} is {state}{where}: supply it in this deployment manifest."
            )
        if runtime_env is not None:
            return human(
                f"{var} is {state}{where}: set it where this deploy supplies "
                "its environment, before the check runs."
            )
        if has_default and not secret:
            return agent(f"Set {var}{where} to the schema's defaultValue.")
        kind = "a secret" if secret else "a value"
        return human(f"{var} is {state}{where}: a person must set it ({kind}).")
    if reason == "file_not_found":
        return human(
            f"Create {file or 'the file'} with the variables this service requires."
        )
    if reason == "invalid_value":
        return human(f"Fix {var}'s value{where}: {detail}.")
    if reason == "invalid_fallback":
        return agent(f"Fix the fallback for {var}{where}: {detail}.")
    if reason == "extra":
        return agent(f"Declare {var} in env.schema.toml, or remove it{where}.")
    if reason == "out_of_scope":
        return agent(
            f"Grant {var} to '{service}' in env.schema.toml (its services list), "
            f"or remove it{where}."
        )
    if reason == "undeclared_reference":
        return agent(
            f'Declare {var} in env.schema.toml with services = ["{service}"], '
            "or stop reading it."
        )
    if reason == "host_forward":
        return human(
            f"Confirm the deploy host sets {var}; a static check can't see it."
        )
    if reason == "external_reference" and (detail or "").endswith(_REFERENCE_SUFFIX):
        form = detail.split(";", 1)[0]
        if runtime_env is not None:
            return human(
                f"{var} is {form}{where}: start the app and this check through "
                "the secret manager that resolves it."
            )
        return human(
            f"{var} is {form}{where}: to check its value, run 'envshield check "
            "--process-env' through your secret manager's run command."
        )
    if reason == "external_reference":
        return human(f"Confirm {var} is supplied by the cluster: {detail}.")
    if reason == "value_not_inspected":
        return human(f"Confirm {var}'s value{where} is valid: {detail}.")
    if reason == "ambiguous_requirement":
        return human(f"{var} is {detail}: confirm whether it's needed.")
    if reason == "source_parse_error":
        return agent(f"Fix {file or 'the source'} so it parses: {detail}")
    if reason == "contract_error":
        return agent(f"Fix the schema: {detail}")
    if reason == "topology_error":
        return agent(f"Fix envshield.yml: {detail}")
    if reason == "source_unreadable":
        return human(f"Make {file or 'the source'} readable: {detail}")
    return human(f"EnvShield couldn't evaluate this; please report it: {detail}")
