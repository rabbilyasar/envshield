# envshield/parsers/_base.py
# Defines the abstract base class for all file parsers, and the typed value
# states every parser reports per key.

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Set, Tuple, Union

from ..core.exceptions import PipeNotServedError

# --- Value states (v5 plan §4) ---------------------------------------------
# What one source says about one key. A key missing from a ValueStates
# mapping is Absent: the source doesn't bind it. Only Concrete carries a
# value read from the source; no field holding manifest or file text is
# ever part of a repr.


@dataclass(frozen=True)
class Concrete:
    """A value EnvShield actually read, including the empty string."""

    value: str = field(repr=False)


@dataclass(frozen=True)
class Forwarded:
    """
    The value comes from the deploy host's environment (Compose
    interpolation, or a bare Compose KEY). `operator` is None, "-", ":-",
    "?", ":?", "+" or ":+"; `fallback` is the manifest's own text
    after the operator, if any (a default for "-"/":-", an error message
    for "?"/":?", the alternative for "+"/":+").
    """

    var: str
    operator: Optional[str] = None
    fallback: Optional[str] = field(default=None, repr=False)
    # The value is text with references inside it ('http://${HOST}:80');
    # `var` then lists every host variable it names, comma-separated.
    embedded: bool = False


@dataclass(frozen=True)
class External:
    """
    The value lives in an object outside the manifest. `kind` is the
    Kubernetes field that points at it (secretKeyRef, configMapKeyRef,
    fieldRef, resourceFieldRef, secretRef, configMapRef); `ref` names it.
    As an unbound entry, kind UNSERVED_PIPE: `ref` is a local file that is
    a named pipe nobody served, so none of its keys could be read (BL-214).
    """

    ref: str
    kind: str


UNSERVED_PIPE = "unservedPipe"


@dataclass(frozen=True)
class KeyPresent:
    """The key exists in an object in this manifest, but its value isn't inspected."""

    ref: str


@dataclass(frozen=True)
class Referenced:
    """
    The text read is a secret manager's reference or an encrypted value
    (BL-213): 'op://...', SOPS 'ENC[...]', ... The real value is resolved
    or decrypted elsewhere, so EnvShield never sees it. `form` names the
    kind ("a 1Password reference"); the text itself is never kept.
    """

    form: str


@dataclass(frozen=True)
class FromSecretManager:
    """
    A variable that comes from the team's secret manager (V3-3, #22), as
    the secrets probe found it, with no value: `status` is "valid",
    "invalid", "blank", "missing" or "referenced" (what the injected value
    was), "unchecked" (the probe didn't run) or "failed" (the secrets
    command failed). `detail` is value-free: a constraint, a reference's
    form, or why it wasn't checked.
    """

    status: str
    detail: Optional[str] = None


# Statuses that say what the secret manager supplies, so they override the
# local file's value; the others leave a local value in place.
SUPPLIED_STATUSES = ("valid", "invalid", "blank", "referenced")


ValueState = Union[
    Concrete, Forwarded, External, KeyPresent, Referenced, FromSecretManager
]

# Whole-value forms that stand for a value EnvShield can't see (BL-213).
# Only distinctive prefixes or shapes: an ordinary URL ('https://...') or
# string never matches.
_REFERENCE_FORMS = (
    (re.compile(r"op://\S+"), "a 1Password reference"),
    (re.compile(r"(?:sm|berglas)://\S+"), "a Berglas (Google Cloud) reference"),
    # bank-vaults' 'vault:secret/data/db#key'; a path, so an image tag
    # such as 'vault:1.15' isn't one.
    (re.compile(r"vault:[^\s/]+/\S*"), "a Vault reference"),
    (re.compile(r"encrypted:[A-Za-z0-9+/=_-]{16,}"), "a dotenvx-encrypted value"),
    (re.compile(r"ENC\[[A-Za-z0-9_]+,data:.*\]", re.DOTALL), "a SOPS-encrypted value"),
)


def reference_form(text: str) -> Optional[str]:
    """What kind of reference or encrypted value `text` is, or None."""
    for pattern, form in _REFERENCE_FORMS:
        if pattern.fullmatch(text):
            return form
    return None


def read_value(text: str) -> ValueState:
    """
    The state of a value read from a source: Concrete, unless the text is
    a reference or an encrypted value (Referenced). Every parser and
    caller that reads a value goes through this.
    """
    form = reference_form(text)
    return Referenced(form) if form else Concrete(text)


class ValueStates(Dict[str, ValueState]):
    """
    One source's states, by key. `unbound` lists references outside the
    file that may bind keys this source can't enumerate (a Kubernetes
    envFrom to a ConfigMap/Secret not in the manifest): a key not in the
    mapping is then not knowably absent. `missing_files` names files the
    source reads that don't exist (a Compose env_file), so a key absent
    from it is reported as such.
    """

    def __init__(
        self,
        states: Mapping[str, ValueState] = (),
        unbound: Iterable[External] = (),
        missing_files: Iterable[str] = (),
    ):
        super().__init__(states)
        self.unbound: Tuple[External, ...] = tuple(unbound)
        self.missing_files: Tuple[str, ...] = tuple(missing_files)


def as_states(values: Mapping[str, Union[str, ValueState]]) -> ValueStates:
    """`values` as ValueStates; a plain string is read with read_value."""
    if isinstance(values, ValueStates):
        return values
    return ValueStates(
        {k: read_value(v) if isinstance(v, str) else v for k, v in values.items()}
    )


def legacy_text(state: ValueState) -> Optional[str]:
    """
    The string v4.8.0 used for `state`, for the v1 report fields and
    `results` (core/verdicts.py judges states directly): a Concrete value,
    or a Compose "-"/":-" fallback. None where v4.8.0 used a placeholder:
    the value isn't visible.
    """
    if isinstance(state, Concrete):
        return state.value
    if isinstance(state, Forwarded) and state.operator in ("-", ":-"):
        return state.fallback
    return None


def not_inspected_names(states: Mapping[str, ValueState]) -> Set[str]:
    """Keys whose deployed value EnvShield can't see (R0-5): every state but
    a Concrete value."""
    return {key for key, state in states.items() if not isinstance(state, Concrete)}


class BaseParser(ABC):
    """
    Abstract Base Class for file parsers.
    Ensures that every parser implements a 'get_vars' method.
    """

    # True for a parser whose target is a deployment manifest (docker-
    # compose, Kubernetes) rather than a local config file (.env, a Python
    # config module). schema_manager.check_target uses this to pick a
    # suggestion message that fits the actual target -- "run 'envshield
    # setup'" is actively wrong advice for a manifest, since setup only
    # ever writes a service's local file, never a deployment manifest.
    is_deployment_manifest = False

    @abstractmethod
    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> Union[Set[str], Dict[str, str]]:
        """
        Parses a file and extracts the set of defined variable names.

        Args:
            file_path: The path to the file to be parsed.

        Returns:
            A set of strings, where each string is a variable name.

        Raises:
            FileNotFoundError: If the file_path does not exist.
        """
        pass

    def get_states(self, file_path: str) -> ValueStates:
        """Each key's ValueState. A local file reads every value: Concrete, or
        Referenced for a reference or an encrypted value."""
        try:
            return as_states(self.get_vars(file_path, get_values=True))
        except PipeNotServedError:
            # Nothing was read, so no key is knowably absent (BL-214).
            return ValueStates(unbound=[External(file_path, UNSERVED_PIPE)])
