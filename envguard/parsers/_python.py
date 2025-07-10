# A parser for Python configuration files using the Abstract Syntax Tree (ast) module.

import ast
import os
from typing import Set
from ._base import BaseParser

class PythonParser(BaseParser):
    """
    Parses Python files to find top-level variable assignments.
    """

    def get_vars(self, file_path: str) -> Set[str]:
        """
        Uses the ast module to safely parse a Python file and find all
        top-level variable assignments (e.g. `SECRET_KEY = "...").
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        variables = set()
        with open(file_path, 'r') as f:
            try:
                # Parse the file content on AST
                tree = ast.parse(f.read(), filename=file_path)

                # Walk through the top-level nodes in the tree
                for node in tree.body:
                    # We are only insterested in the assignment statements
                    if isinstance(node, ast.Assign):
                        # An assignement can have multiple targets(e.g. a=b=3)
                        for target in node.targets:
                            # We only care about simple name assignments (e.g. VAR=...)
                            if isinstance(target, ast.Name):
                                variables.add(target.id)
            except (SyntaxError, TypeError) as e:
                # Handles cases where the file isn't a valid python
                print(f"Warning: Could not parse Python file '{file_path}': {e}")
                return set()
        return variables