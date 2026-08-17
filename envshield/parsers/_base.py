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
