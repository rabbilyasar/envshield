# envshield/parsers/factory.py
# A factory to select the appropriate parser based on file extension.

import os
from typing import Optional
from ._base import BaseParser
from ._dotenv import DotenvParser
from ._python import PythonParser


def get_parser(file_path: str) -> Optional[BaseParser]:
    """
    Selects and returns the correct parser instance based on the file extension.

    Args:
        file_path: The path to the file that needs parsing.

    Returns:
        An instance of a BaseParser subclass, or None if no suitable
        parser is found.
    """
    _, extension = os.path.splitext(file_path)

    if extension == ".py":
        return PythonParser()
    # Assume files with no extension (like '.env') or '.env' extension are dotenv files
    elif extension == "" or ".env" in file_path:
        return DotenvParser()

    # In the future, we can add more parsers here (e.g., for .json, .toml)

    return None
