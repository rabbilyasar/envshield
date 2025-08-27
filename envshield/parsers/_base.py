# envshield/parsers/_base.py
# Defines the abstract base class for all file parsers.

from abc import ABC, abstractmethod
from typing import Set


class BaseParser(ABC):
    """
    Abstract Base Class for file parsers.
    Ensures that every parser implements a 'get_vars' method.
    """

    @abstractmethod
    def get_vars(self, file_path: str) -> Set[str]:
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
