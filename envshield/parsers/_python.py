# envshield/parsers/_python.py
# A parser for Python configuration files using the Abstract Syntax Tree (ast) module.

import ast
import os
from typing import Dict, Set, Union

from ..core.exceptions import EnvShieldException
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
            except (SyntaxError, TypeError, UnicodeDecodeError) as e:
                # A malformed or non-UTF-8 file can never safely be treated
                # as "declares nothing" -- every caller that diffs this
                # result against a schema would silently read that as a
                # false-clean (see BL-002/BL-092). Raise instead, matching
                # DockerComposeParser.get_vars's existing pattern for the
                # equivalent case. str(e) only -- never an attribute like
                # SyntaxError.text -- so the file's actual content, which
                # may contain a secret value, is never echoed here.
                raise EnvShieldException(f"Could not parse '{file_path}': {e}")

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
