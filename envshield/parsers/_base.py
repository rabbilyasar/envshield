# envshield/parsers/_base.py
# Defines the abstract base class for all file parsers.

from abc import ABC, abstractmethod
from typing import Dict, Set, Union


class BaseParser(ABC):
    """
    Abstract Base Class for file parsers.
    Ensures that every parser implements a 'get_vars' method.
    """

    # Shared sentinel for "this variable is declared here, but its real
    # value legitimately lives outside this file" -- an env_file reference,
    # a bare pass-through-from-shell entry, a Kubernetes secretRef, or an
    # unresolvable interpolation. Every parser that can produce this uses
    # the exact same string (rather than each defining its own copy) so
    # schema_manager.diff_against_schema can recognize it by identity and
    # skip type/enum/pattern validation -- validating a placeholder string
    # against a declared type would otherwise always fail, misreporting a
    # legitimately-unknown value as an invalid one.
    UNRESOLVED_VALUE = "<value not visible in this file>"

    # Set by a parser's own get_vars() (as an instance attribute, shadowing
    # this class-level default) when the file references an external
    # source -- e.g. a Kubernetes envFrom.configMapRef/secretRef whose
    # ConfigMap/Secret isn't defined anywhere in the supplied manifest --
    # that could supply variable NAMES this parser has no way to enumerate,
    # not just a value it can't resolve for an already-known name (that
    # case is UNRESOLVED_VALUE, above). Distinct from UNRESOLVED_VALUE
    # because it can't be expressed as a per-key entry in the returned
    # dict at all: there's no key to attach it to when the possible
    # variable names themselves are unknown. Left False (the default) for
    # every parser that has no such concept -- schema_manager.
    # diff_against_schema treats a required variable this parser didn't
    # report as genuinely missing unless this is True, in which case it's
    # reported as unresolved instead: EnvShield cannot confirm the
    # variable is supplied, but must equally not claim it's confirmed
    # absent.
    has_unresolved_source = False

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
