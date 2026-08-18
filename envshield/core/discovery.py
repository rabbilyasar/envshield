# envshield/core/discovery.py
"""
Configuration Discovery (Phase 2B): source-code detection of
environment-variable reads, independent of the CLI and of any one schema.

Milestone 1 (Python): AST-based, replacing the regex stopgap that used to
live in scanner.py's USAGE_PATTERNS for os.environ.get/os.getenv.
Milestone 2 (JS/TS): regex plus a bounded linear brace-matching pass --
deliberately not tree-sitter (see the Milestone 2 design notes); the
grammar this milestone needs (property access, single-level object
destructuring) is narrow enough that a real parser dependency isn't
justified yet.

This module knows nothing about schemas, diffing, staged-vs-disk Git
content, or the CLI -- it takes source text already read by its caller and
returns normalized usage records. Deliberately narrow for both milestones:
literal keys/property names only, no alias or import-indirection tracking,
no dynamic-key resolution, no nested destructuring. Each of those is a
deliberate scope boundary, not an oversight -- see the Phase 2B plans for why.
"""

import ast
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional


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
        if _is_os_environ(node.value) and isinstance(node.ctx, ast.Load):
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


def _js_language_for(file_path: str) -> Optional[str]:
    if file_path.endswith((".ts", ".tsx")):
        return "typescript"
    if file_path.endswith((".js", ".jsx")):
        return "javascript"
    return None


_JS_DOT_ACCESS_RE = re.compile(r"process\.env\.(\w+)")
_JS_BRACKET_ACCESS_RE = re.compile(r"process\.env\[\s*(['\"])(\w+)\1\s*\]")
_JS_META_DOT_ACCESS_RE = re.compile(r"import\.meta\.env\.(\w+)")
_JS_META_BRACKET_ACCESS_RE = re.compile(r"import\.meta\.env\[\s*(['\"])(\w+)\1\s*\]")
_JS_DESTRUCTURE_SOURCE_RE = re.compile(r"=\s*(process\.env|import\.meta\.env)\b")
_JS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")

# A plain '=' immediately following a dot/bracket-access match (not part of
# '==='/'=='), possibly after whitespace -- the match is an assignment
# target (a write), not a read. Deliberately narrow, matching this milestone's
# regex-based scope: compound assignment ('+=', '-=', etc.) is not
# recognized as a write and is still reported as a read, an accepted
# under-detection rather than a false negative on the far more common case.
_JS_ASSIGNMENT_TARGET_RE = re.compile(r"\s*=(?!=)")

# A single-level object-destructuring pattern in real code is never
# anywhere close to this long -- this is defense-in-depth against a
# pathologically constructed span, not a bound expected to ever trigger on
# genuine source.
_MAX_DESTRUCTURE_SPAN = 5000


def _build_brace_match_and_line_index(
    content: str,
) -> "tuple[Dict[int, int], List[int]]":
    """
    One linear pass over `content`: maps every '}' position to its
    matching '{' position (a standard stack-based bracket match -- an
    unmatched '}' is simply not recorded, an unmatched '{' is simply left
    on the stack, both handled gracefully with no error), and separately
    records every newline's position for O(log n) line-number lookups
    afterward (see `_line_at`) instead of re-scanning from the start of the
    file for every match.

    This is deliberately not a real tokenizer -- it doesn't know about
    strings, comments, or regex literals, so a stray brace character inside
    one of those could in principle mis-match a nearby destructuring
    pattern. Regex-based scanning already has this exact class of
    limitation everywhere else in scanner.py (e.g. a secret pattern
    matching inside a comment); not solved here either, for the same reason
    -- it would mean writing a real lexer, which this milestone's scope
    explicitly avoids.
    """
    stack: List[int] = []
    matches: Dict[int, int] = {}
    newline_positions: List[int] = []
    for i, ch in enumerate(content):
        if ch == "{":
            stack.append(i)
        elif ch == "}":
            if stack:
                matches[i] = stack.pop()
        elif ch == "\n":
            newline_positions.append(i)
    return matches, newline_positions


def _line_at(newline_positions: List[int], pos: int) -> int:
    return bisect_right(newline_positions, pos) + 1


def _preceding_non_whitespace(content: str, pos: int) -> int:
    i = pos - 1
    while i >= 0 and content[i].isspace():
        i -= 1
    return i


def _destructured_keys(span: str) -> List[str]:
    """
    Depth-aware comma split of a single-level object-destructuring
    pattern's inner content (the text between '{' and '}'), returning only
    the real process.env/import.meta.env property names -- not rename
    targets, not default values, not a rest element ('...rest' is skipped
    entirely: it names no specific variable, the same reasoning that
    already excludes dynamic keys elsewhere in this module).

    Depth-aware so a default value that's itself an object/array/call
    ('{ A = someCall(1, 2) }') doesn't get wrongly split on its own commas.
    Not quote-aware: a comma inside a quoted default string ('{ A = "a,b" }')
    would split incorrectly -- an accepted, narrow edge case (env-var
    defaults are almost never comma-containing strings), and even then the
    key name itself ('A') is still extracted correctly, since it's taken
    from before the first ':'/'=' regardless of how the remainder splits.
    """
    entries: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in span:
        if ch in "{[(":
            depth += 1
            current.append(ch)
        elif ch in "}])":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            entries.append("".join(current))
            current = []
        else:
            current.append(ch)
    entries.append("".join(current))

    keys = []
    for entry in entries:
        entry = entry.strip()
        if not entry or entry.startswith("..."):
            continue
        key = re.split(r"[:=]", entry, maxsplit=1)[0].strip()
        if _JS_IDENTIFIER_RE.fullmatch(key):
            keys.append(key)
    return keys


def _discover_js_destructuring(
    content: str,
    file_path: str,
    language: str,
    brace_matches: Dict[int, int],
    newline_positions: List[int],
) -> List[DiscoveredVariableUsage]:
    usages = []

    for m in _JS_DESTRUCTURE_SOURCE_RE.finditer(content):
        source = "process.env{}" if m.group(1) == "process.env" else "import.meta.env{}"
        close_pos = _preceding_non_whitespace(content, m.start())
        if close_pos < 0 or content[close_pos] != "}":
            continue
        open_pos = brace_matches.get(close_pos)
        if open_pos is None:
            continue
        span = content[open_pos + 1 : close_pos]
        if len(span) > _MAX_DESTRUCTURE_SPAN:
            continue
        statement_line = _line_at(newline_positions, open_pos)
        for key in _destructured_keys(span):
            usages.append(
                DiscoveredVariableUsage(
                    variable=key,
                    file_path=file_path,
                    line=statement_line,
                    language=language,
                    access_type=source,
                    confidence="high",
                )
            )

    return usages


def discover_js_usages(content: str, file_path: str) -> List[DiscoveredVariableUsage]:
    """
    The one public entry point for JS/TS (Phase 2B Milestone 2): recognizes
    process.env/import.meta.env dot access, bracket access, and
    single-level object destructuring (including rename/default/rest
    handling) with a literal string key/property name. Gated by the
    caller to .js/.jsx/.ts/.tsx files; `language` is derived from the
    extension purely for labeling -- none of these patterns differ between
    JS and TS, so no TS-specific syntax awareness is needed.

    Regex-based, not a parser -- never raises on malformed/unusual input;
    it just matches fewer (or zero) usages, the same safe-by-construction
    property every other regex-based check in scanner.py already has.
    """
    language = _js_language_for(file_path) or "javascript"
    # Built once and reused for every match below -- a per-match
    # content.count("\n", 0, pos) call would cost O(pos) each time, which
    # for a file with many matches spread across it degrades toward
    # O(n * matches) rather than the O(n) this achieves.
    brace_matches, newline_positions = _build_brace_match_and_line_index(content)
    usages = []

    for pattern, group, access_type in (
        (_JS_DOT_ACCESS_RE, 1, "process.env."),
        (_JS_BRACKET_ACCESS_RE, 2, "process.env[]"),
        (_JS_META_DOT_ACCESS_RE, 1, "import.meta.env."),
        (_JS_META_BRACKET_ACCESS_RE, 2, "import.meta.env[]"),
    ):
        for m in pattern.finditer(content):
            if _JS_ASSIGNMENT_TARGET_RE.match(content, m.end()):
                continue
            usages.append(
                DiscoveredVariableUsage(
                    variable=m.group(group),
                    file_path=file_path,
                    line=_line_at(newline_positions, m.start()),
                    language=language,
                    access_type=access_type,
                    confidence="high",
                )
            )

    usages.extend(
        _discover_js_destructuring(
            content, file_path, language, brace_matches, newline_positions
        )
    )
    return usages
