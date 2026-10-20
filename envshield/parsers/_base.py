# envshield/parsers/_base.py
# Defines the abstract base class for all file parsers, and the typed value
# states every parser reports per key.

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional, Set, Tuple, Union

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
    """

    ref: str
    kind: str


@dataclass(frozen=True)
class KeyPresent:
    """The key exists in an object in this manifest, but its value isn't inspected."""

    ref: str


ValueState = Union[Concrete, Forwarded, External, KeyPresent]


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
    """`values` as ValueStates; a plain string is a Concrete value."""
    if isinstance(values, ValueStates):
        return values
    return ValueStates(
        {k: Concrete(v) if isinstance(v, str) else v for k, v in values.items()}
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
        """Each key's ValueState. A local file reads every value: all Concrete."""
        return as_states(self.get_vars(file_path, get_values=True))
