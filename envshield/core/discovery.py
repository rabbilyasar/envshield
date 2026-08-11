# envshield/core/discovery.py
"""
Configuration Discovery (Phase 2B, Milestone 1): AST-based detection of
environment-variable reads in Python source, replacing the regex stopgap
that used to live in scanner.py's USAGE_PATTERNS for os.environ.get/
os.getenv (see CLAUDE.md SS6B).

This module knows nothing about schemas, diffing, staged-vs-disk Git
content, or the CLI -- it takes source text already read by its caller and
returns normalized usage records. Deliberately narrow for this milestone:
only the literal os.environ/os.getenv attribute chain, only a positional
literal-string key/first-argument, no alias or import-indirection tracking,
no dynamic-key resolution. Each of those is a deliberate scope boundary, not
an oversight -- see the Phase 2B Milestone 1 plan for why.
"""

import ast
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DiscoveredVariableUsage:
    variable: str
    file_path: str
    line: int
    language: str
    access_type: str
    confidence: str

    def to_dict(self) -> dict:
        return {
            "variable": self.variable,
            "file_path": self.file_path,
            "line": self.line,
            "language": self.language,
            "access_type": self.access_type,
            "confidence": self.confidence,
        }


def _literal_str(node: Optional[ast.expr]) -> Optional[str]:
    """
    Returns the string value of `node` if it's a plain literal string
    constant, else None -- deliberately not resolving names, f-strings,
    concatenation-with-a-name, or any other indirection.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_os_environ(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_getenv(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "getenv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_environ_get(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "get"
        and _is_os_environ(node.value)
    )


class _UsageVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.usages: List[DiscoveredVariableUsage] = []

    def _record(self, variable: str, line: int, access_type: str) -> None:
        self.usages.append(
            DiscoveredVariableUsage(
                variable=variable,
                file_path=self.file_path,
                line=line,
                language="python",
                access_type=access_type,
                confidence="high",
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if not node.args:
            self.generic_visit(node)
            return

        key = _literal_str(node.args[0])
        if key is not None:
            if _is_os_environ_get(node.func):
                self._record(key, node.lineno, "os.environ.get")
            elif _is_os_getenv(node.func):
                self._record(key, node.lineno, "os.getenv")

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value):
            key = _literal_str(node.slice)
            if key is not None:
                self._record(key, node.lineno, "os.environ[]")

        self.generic_visit(node)


def discover_python_usages(
    content: str, file_path: str
) -> List[DiscoveredVariableUsage]:
    """
    The one public entry point: parses `content` (already-read Python
    source -- this function never touches the filesystem itself, so it
    works identically for staged-index content and on-disk content) and
    returns every recognized os.environ.get/os.getenv/os.environ[] usage
    with a literal string key.

    Returns an empty list -- never raises -- if `content` isn't valid
    Python (SyntaxError) or is pathologically deep (RecursionError): a file
    this can't discover usages in should not abort the overall scan or
    affect that same file's secret-scanning results.
    """
    try:
        tree = ast.parse(content, filename=file_path)
    except (SyntaxError, RecursionError, ValueError):
        return []

    visitor = _UsageVisitor(file_path)
    try:
        visitor.visit(tree)
    except RecursionError:
        return []

    return visitor.usages
