# envshield/parsers/_python.py
# A parser for Python configuration files using the Abstract Syntax Tree (ast) module.

import ast
import os
from typing import Dict, Set, Union

from ._base import BaseParser


class PythonParser(BaseParser):
    """
    Parses Python files to find top-level variable assignments.
    """

    def get_vars(
        self, file_path: str, get_values: bool = False
    ) -> Union[Set[str], Dict[str, str]]:
        """
        Uses the ast module to safely parse a Python file and find all
        top-level variable assignments (e.g., `SECRET_KEY = "..."`).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        variables: Dict[str, str] = {}
        with open(file_path, "r") as f:
            try:
                # Parse the file content into an AST
                tree = ast.parse(f.read(), filename=file_path)

                # Walk through the top-level nodes in the tree
                for node in tree.body:
                    # We are only interested in assignment statements
                    if isinstance(node, ast.Assign):
                        # An assignment can have multiple targets (e.g., a = b = 10)
                        for target in node.targets:
                            # We only care about simple name assignments (e.g., VAR = ...)
                            if isinstance(target, ast.Name):
                                variables[target.id] = self._resolve_value(node.value)
            except (SyntaxError, TypeError) as e:
                # Handle cases where the file is not valid Python
                print(f"Warning: Could not parse Python file '{file_path}': {e}")
                return {} if get_values else set()

        return variables if get_values else set(variables.keys())

    @staticmethod
    def _resolve_value(node: ast.expr) -> str:
        """
        Best-effort extraction of a literal value's string representation.
        Non-literal expressions (e.g. `os.getenv(...)`, function calls) can't be
        safely evaluated, so they resolve to an empty string rather than raising.
        """
        try:
            return str(ast.literal_eval(node))
        except (ValueError, TypeError):
            return ""
