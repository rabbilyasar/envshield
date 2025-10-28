# envshield/parsers/_dotenv.py
# A simple parser for key-value .env files.
import os
from typing import Set, Dict, Union
from ._base import BaseParser


class DotenvParser(BaseParser):
    """
    Parses traditional .env files.
    """

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> Union[Set[str], Dict[str, str]]:
        """
        Extracts variable names from a .env file.
        - Ignores lines starting with '#' (comments).
        - Ignores empty lines.
        - Splits lines by the first '=' to get the key.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        variables = {}
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key:
                        variables[key] = value.strip()

        return variables if get_values else set(variables.keys())
