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
literal keys/property names only, no dynamic-key resolution, no nested
destructuring. Each of those is a deliberate scope boundary, not an
oversight -- see the Phase 2B plans for why.

One narrow, evidence-based exception to "no alias tracking" (BL-113): an
import-time rename of Flask's `current_app` (`from flask import
current_app as app`) is tracked, because cross-codebase evidence found it
to be the *dominant* real-world spelling of a Flask config read, not a
rare exception -- see _FlaskBindings. This does not extend to any other
alias or import-indirection form (an assigned alias like `config =
current_app.config` remains untracked, matching this module's existing
philosophy everywhere else).
"""

import ast
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional


@dataclass
class DiscoveredVariableUsage:
    """
    `confidence` ("high" or "medium") is **not** a fuzzy certainty score
    about whether this line ultimately reads an environment variable -- it
    names which of two structurally different claims this usage makes:

    - `"high"`: this line unambiguously reads `variable` directly from the
      process environment (`os.environ`/`os.getenv`/`process.env`). This
      claim is exact; nothing about it varies in strength.
    - `"medium"`: this line reads `variable` from a *config object*
      (currently, Flask's `current_app.config`) whose contents came from
      somewhere unspecified -- a literal, a file, an object, possibly (but
      not provably, from this line alone) an environment variable. This is
      not "a slightly less certain environment read" -- it is a different
      claim about a different kind of evidence, and callers must not blend
      the two into a single "how sure are we" scale.

    This distinction is exactly why `"medium"` usages are excluded from
    every binary completeness/validation path (`undeclared`'s missing-
    declaration detection in dependency_snapshot.py, `scan`'s undeclared-
    variable listing in scanner.py -- both filter to `confidence == "high"`
    before their own logic runs) and surfaced only through `explain`,
    where a human reads the caveat directly rather than a pass/fail gate
    silently deciding what it means.
    """

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


@dataclass
class _OsBindings:
    """
    Whether this module has an unaliased 'from os import getenv'/'from os
    import environ' -- the one bare-import form worth recognizing as
    equivalent to os.getenv/os.environ. An aliased import ('from os import
    getenv as ge') is deliberately out of scope, matching this module's
    existing precedent for BaseSettings (_is_base_settings_subclass):
    resolved by the bound local name only, not by tracing the true origin
    of an alias further. Computed once per file by _collect_os_bindings and
    threaded through every predicate below so discover_python_usages and
    discover_python_env_vars can never disagree on what counts as a read.
    """

    bare_getenv: bool = False
    bare_environ: bool = False


def _scan_os_import_nodes(nodes) -> _OsBindings:
    bindings = _OsBindings()
    for node in nodes:
        if not (
            isinstance(node, ast.ImportFrom) and node.module == "os" and node.level == 0
        ):
            continue
        for alias in node.names:
            if alias.asname is not None:
                continue  # aliased: out of scope, see _OsBindings
            if alias.name == "getenv":
                bindings.bare_getenv = True
            elif alias.name == "environ":
                bindings.bare_environ = True
    return bindings


def _collect_os_bindings(tree: ast.Module, content: str) -> _OsBindings:
    """
    `tree.body` (top-level statements) first -- cheap, and covers the
    overwhelmingly common case. A real-world false negative found against
    Zeus (a genuine security-relevant read, `getenv("BYPASS_MFA")` behind a
    function-local `from os import getenv`) showed the module-level-only
    scan this used to be limited to isn't actually rare enough to skip --
    the same conclusion BL-113's evidence already reached for Flask's
    `current_app` bindings (see _FlaskBindings/_collect_flask_bindings),
    which this now mirrors: a full ast.walk only runs when the top-level
    scan found nothing AND a cheap `"from os import" in content` prefilter
    confirms it's even possible, so a file with no such import anywhere
    (the common case for a module already covered by the top-level scan,
    or one that only ever does `import os`) never pays the extra
    traversal.
    """
    bindings = _scan_os_import_nodes(tree.body)
    if bindings.bare_getenv and bindings.bare_environ:
        return bindings  # nothing left a full walk could add
    if "from os import" not in content:
        return bindings
    full = _scan_os_import_nodes(ast.walk(tree))
    return _OsBindings(
        bare_getenv=bindings.bare_getenv or full.bare_getenv,
        bare_environ=bindings.bare_environ or full.bare_environ,
    )


def _is_os_environ(node: ast.expr, bindings: _OsBindings) -> bool:
    if bindings.bare_environ and isinstance(node, ast.Name) and node.id == "environ":
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_getenv(node: ast.expr, bindings: _OsBindings) -> bool:
    if bindings.bare_getenv and isinstance(node, ast.Name) and node.id == "getenv":
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "getenv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_environ_get(node: ast.expr, bindings: _OsBindings) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "get"
        and _is_os_environ(node.value, bindings)
    )


@dataclass
class _FlaskBindings:
    """
    Local names bound to Flask's `current_app` via `from flask import
    current_app` or `from flask import current_app as <alias>`.

    Unlike _OsBindings, an aliased import is deliberately tracked here, not
    excluded -- BL-113's cross-codebase evidence (two independent real
    Flask applications) found `from flask import current_app as app` is
    the *dominant* real-world form (84-89% of measured Flask-config reads
    in each), not the rare exception os's own aliasing is. Also unlike
    _OsBindings/_collect_os_bindings, this is collected from the entire
    file (see _collect_flask_bindings), not just top-level statements --
    the same evidence found most `from flask import current_app` imports
    are function-local, deferred specifically to avoid Flask's
    app-context/circular-import issues at module load time, not a rare
    edge case the way a deferred `from os import ...` is.

    Deliberately does NOT track: a locally constructed `Flask(...)`/
    Flask-subclass instance (evidence found this catches almost no real
    reads -- most real code either aliases `current_app` at import time or
    receives an app object as a function parameter, neither of which a
    local-construction check can see), `self.config` inside a Flask
    subclass's own methods, module-qualified `flask.current_app` (measured
    zero occurrences in both evidence codebases), and assignment-style
    aliasing such as `config = current_app.config` (measured zero
    occurrences in both evidence codebases, and out of scope for the same
    reason this module tracks no other alias/import-indirection).
    """

    current_app_names: FrozenSet[str] = frozenset()


def _collect_flask_bindings(tree: ast.Module) -> _FlaskBindings:
    """
    A full ast.walk, not tree.body -- see _FlaskBindings for why a
    top-level-only scan (matching _collect_os_bindings' own choice for
    `os`) would miss the majority of real Flask evidence. Callers gate
    this behind a cheap `"current_app" in content` text check first (see
    discover_python_usages) so a file with no Flask involvement at all
    never pays this extra traversal.
    """
    names = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "flask"
            and node.level == 0
        ):
            continue
        for alias in node.names:
            if alias.name == "current_app":
                names.add(alias.asname or alias.name)
    return _FlaskBindings(current_app_names=frozenset(names))


def _is_flask_config_attr(node: ast.expr, flask_bindings: _FlaskBindings) -> bool:
    """
    `<name>.config` where `<name>` is a bare local name bound to
    `current_app` (see _FlaskBindings) -- deliberately restricted to a
    bare ast.Name base, the same shape _is_os_environ already requires for
    `os`, which is exactly what already excludes a real false-positive
    found in evidence (`self.gateway.config`, an unrelated object's own
    settings dict): its base is an ast.Attribute chain (`self.gateway`),
    never a bare Name, so it can never match here regardless of what
    `flask_bindings` contains.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "config"
        and isinstance(node.value, ast.Name)
        and node.value.id in flask_bindings.current_app_names
    )


def _is_flask_config_get(node: ast.expr, flask_bindings: _FlaskBindings) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "get"
        and _is_flask_config_attr(node.value, flask_bindings)
    )


class _UsageVisitor(ast.NodeVisitor):
    def __init__(
        self, file_path: str, bindings: _OsBindings, flask_bindings: _FlaskBindings
    ):
        self.file_path = file_path
        self.bindings = bindings
        self.flask_bindings = flask_bindings
        self.usages: List[DiscoveredVariableUsage] = []

    def _record(
        self, variable: str, line: int, access_type: str, confidence: str = "high"
    ) -> None:
        self.usages.append(
            DiscoveredVariableUsage(
                variable=variable,
                file_path=self.file_path,
                line=line,
                language="python",
                access_type=access_type,
                confidence=confidence,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if not node.args:
            self.generic_visit(node)
            return

        key = _literal_str(node.args[0])
        if key is not None:
            if _is_os_environ_get(node.func, self.bindings):
                self._record(key, node.lineno, "os.environ.get")
            elif _is_os_getenv(node.func, self.bindings):
                self._record(key, node.lineno, "os.getenv")
            elif _is_flask_config_get(node.func, self.flask_bindings):
                # BL-113: one level of indirection through a config object
                # whose contents came from somewhere unspecified (a
                # literal, a file, an environment variable) -- never as
                # certain as a direct os.environ/os.getenv read, hence
                # "medium" rather than "high" (see DiscoveredVariableUsage).
                self._record(
                    key,
                    node.lineno,
                    "flask.current_app.config.get",
                    confidence="medium",
                )

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value, self.bindings) and isinstance(node.ctx, ast.Load):
            key = _literal_str(node.slice)
            if key is not None:
                self._record(key, node.lineno, "os.environ[]")
        elif _is_flask_config_attr(node.value, self.flask_bindings) and isinstance(
            node.ctx, ast.Load
        ):
            key = _literal_str(node.slice)
            if key is not None:
                self._record(
                    key, node.lineno, "flask.current_app.config[]", confidence="medium"
                )

        self.generic_visit(node)


def discover_python_usages(
    content: str, file_path: str
) -> List[DiscoveredVariableUsage]:
    """
    The one public entry point: parses `content` (already-read Python
    source -- this function never touches the filesystem itself, so it
    works identically for staged-index content and on-disk content) and
    returns every recognized os.environ.get/os.getenv/os.environ[] usage
    with a literal string key -- including the same read spelled as a bare
    getenv(...)/environ[...] after an unaliased 'from os import getenv'/
    'from os import environ' (see _OsBindings/_collect_os_bindings) -- plus
    (BL-113) every recognized Flask `current_app.config[...]`/`.get(...)`
    read (see _FlaskBindings), reported at "medium" rather than "high"
    confidence.

    The Flask-specific binding scan (_collect_flask_bindings, a full
    ast.walk) only runs when the substring "current_app" appears in
    `content` at all -- a cheap prefilter so a file with no Flask
    involvement pays no extra traversal cost beyond the parse already
    required for the os-based checks.

    Returns an empty list -- never raises -- if `content` isn't valid
    Python (SyntaxError) or is pathologically deep (RecursionError): a file
    this can't discover usages in should not abort the overall scan or
    affect that same file's secret-scanning results.
    """
    try:
        tree = ast.parse(content, filename=file_path)
    except (SyntaxError, RecursionError, ValueError):
        return []

    bindings = _collect_os_bindings(tree, content)
    flask_bindings = (
        _collect_flask_bindings(tree) if "current_app" in content else _FlaskBindings()
    )
    visitor = _UsageVisitor(file_path, bindings, flask_bindings)
    try:
        visitor.visit(tree)
    except RecursionError:
        return []

    return visitor.usages


@dataclass
class DiscoveredPythonEnvVar:
    """
    An environment-variable read discovered specifically for schema
    generation ('envshield import'/'init') -- richer than
    DiscoveredVariableUsage (used by 'explain'/'undeclared', whose stable
    to_dict() shape this must not touch) because generating a schema needs
    a best-effort recoverable literal default/fallback value, which
    neither of those callers ever needed. Deliberately a separate type
    rather than an optional field bolted onto DiscoveredVariableUsage.
    """

    variable: str
    line: int
    access_type: str
    default_value: Optional[str]


def _literal_default(node: Optional[ast.expr]) -> Optional[str]:
    """
    Best-effort literal default/fallback value, coerced to the string form
    every value in this codebase is ultimately compared/rendered as (see
    schema_types.py) -- string, int, float, and bool constants are all
    accepted. None (including pydantic's Ellipsis-as-"required, no
    default" marker, filtered out by callers before reaching this) yields
    no default, same as any other non-literal expression.
    """
    if isinstance(node, ast.Constant) and node.value is not None:
        return str(node.value)
    return None


def _is_recognized_env_call(node: ast.expr, bindings: _OsBindings) -> bool:
    """Whether `node` is itself a recognized os.environ.get/os.getenv call with a literal key -- reuses the exact predicates _UsageVisitor does, so the two engines can never disagree on what counts as a read."""
    return (
        isinstance(node, ast.Call)
        and bool(node.args)
        and _literal_str(node.args[0]) is not None
        and (
            _is_os_environ_get(node.func, bindings)
            or _is_os_getenv(node.func, bindings)
        )
    )


def _is_recognized_env_subscript(node: ast.expr, bindings: _OsBindings) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and _is_os_environ(node.value, bindings)
        and isinstance(node.ctx, ast.Load)
        and _literal_str(node.slice) is not None
    )


def _contains_recognized_env_read(node: ast.expr, bindings: _OsBindings) -> bool:
    """
    Whether an os.environ.get/os.getenv/os.environ[] read (with a literal
    key) appears anywhere inside `node`'s subtree -- used to decide
    whether a BaseSettings field's own value already names its real env
    var explicitly (which always wins) before falling back to the
    alias/attribute-name convention below.
    """
    return any(
        _is_recognized_env_call(sub, bindings)
        or _is_recognized_env_subscript(sub, bindings)
        for sub in ast.walk(node)
    )


def _is_pydantic_field_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "Field")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "Field")
    )


def _pydantic_field_alias(call_node: ast.Call) -> Optional[str]:
    for kw in call_node.keywords:
        if kw.arg == "alias":
            alias = _literal_str(kw.value)
            if alias is not None:
                return alias
    return None


def _pydantic_field_default(call_node: ast.Call) -> Optional[str]:
    """
    pydantic's Field(default, ...) -- the first positional arg, or an
    explicit 'default=' keyword if there's no positional one. Ellipsis
    ('...') is pydantic's own marker for "required, no default", never a
    literal value to suggest.
    """
    first = call_node.args[0] if call_node.args else None
    if first is None:
        for kw in call_node.keywords:
            if kw.arg == "default":
                first = kw.value
                break
    if isinstance(first, ast.Constant) and first.value is Ellipsis:
        return None
    return _literal_default(first)


def _is_base_settings_subclass(node: ast.ClassDef) -> bool:
    """
    Matches a base named (bare, or the attribute name of a dotted access)
    'BaseSettings' -- deliberately by name only, not by resolving the real
    import, matching this module's existing precedent for os.environ (see
    TestOutOfScopeByDesign): an aliased import ('from pydantic_settings
    import BaseSettings as BS') is out of scope, not a bug.
    """
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseSettings":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseSettings":
            return True
    return False


class _EnvVarWithDefaultsVisitor(ast.NodeVisitor):
    """
    Separate from _UsageVisitor by design: reuses its exact
    node-recognition predicates (so the two engines can never disagree on
    what counts as a read), but additionally recovers a best-effort
    literal default/fallback value and recognizes BaseSettings class
    attributes -- neither of which _UsageVisitor's own callers
    ('explain'/'undeclared') ever needed.
    """

    def __init__(self, file_path: str, bindings: _OsBindings):
        self.file_path = file_path
        self.bindings = bindings
        self.usages: List[DiscoveredPythonEnvVar] = []

    def _record(
        self,
        variable: str,
        line: int,
        access_type: str,
        default_value: Optional[str],
    ) -> None:
        self.usages.append(
            DiscoveredPythonEnvVar(
                variable=variable,
                line=line,
                access_type=access_type,
                default_value=default_value,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if not node.args:
            self.generic_visit(node)
            return

        key = _literal_str(node.args[0])
        if key is not None:
            default = _literal_default(node.args[1]) if len(node.args) > 1 else None
            if _is_os_environ_get(node.func, self.bindings):
                self._record(key, node.lineno, "os.environ.get", default)
            elif _is_os_getenv(node.func, self.bindings):
                self._record(key, node.lineno, "os.getenv", default)

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value, self.bindings) and isinstance(node.ctx, ast.Load):
            key = _literal_str(node.slice)
            if key is not None:
                self._record(key, node.lineno, "os.environ[]", None)

        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """
        'X or "fallback"' -- only the exact two-operand shape, where X is
        itself a recognized read and the right-hand side is a literal.
        Anything else (three-plus operands, a non-literal right-hand side,
        a left-hand side that isn't a recognized read) is deliberately
        left to the ordinary traversal below, recording the read (if any)
        with no default rather than guessing at one -- no evaluation
        semantics are invented here.
        """
        if len(node.values) == 2:
            left, right = node.values
            default = _literal_default(right)
            if default is not None:
                if _is_recognized_env_call(left, self.bindings):
                    key = _literal_str(left.args[0])
                    access_type = (
                        "os.environ.get"
                        if _is_os_environ_get(left.func, self.bindings)
                        else "os.getenv"
                    )
                    self._record(key, left.lineno, access_type, default)
                    return
                if _is_recognized_env_subscript(left, self.bindings):
                    key = _literal_str(left.slice)
                    self._record(key, left.lineno, "os.environ[]", default)
                    return

        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _is_base_settings_subclass(node):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(
                    stmt.target, ast.Name
                ):
                    self._visit_base_settings_field(stmt)
        self.generic_visit(node)

    def _visit_base_settings_field(self, stmt: ast.AnnAssign) -> None:
        # An explicit os.environ/getenv read anywhere in this field's own
        # value always wins over the alias/attribute-name convention below
        # -- it names the real env var directly, and the ordinary
        # visit_Call/visit_Subscript traversal (via generic_visit, still
        # run for this class after this method returns) already records
        # it correctly on its own.
        if stmt.value is not None and _contains_recognized_env_read(
            stmt.value, self.bindings
        ):
            return

        attr_name = stmt.target.id
        if stmt.value is not None and _is_pydantic_field_call(stmt.value):
            variable = _pydantic_field_alias(stmt.value) or attr_name.upper()
            default = _pydantic_field_default(stmt.value)
        else:
            variable = attr_name.upper()
            default = _literal_default(stmt.value) if stmt.value is not None else None

        self._record(variable, stmt.lineno, "pydantic.BaseSettings.field", default)


def discover_python_env_vars(
    content: str, file_path: str
) -> List[DiscoveredPythonEnvVar]:
    """
    The one public entry point for schema generation ('import'/'init'):
    everything discover_python_usages finds (os.environ.get/os.getenv/
    os.environ[], anywhere in the file, including inside a class body),
    plus a best-effort recoverable literal default/fallback value for
    each, plus BaseSettings class-attribute recognition (a bare annotated
    field, or Field(..., alias=...)) -- the one common real-world shape
    that contains no os.environ call at all, which discover_python_usages
    was never designed to see.

    Returns an empty list -- never raises -- under the same conditions as
    discover_python_usages: invalid Python, or pathologically deep input.
    """
    try:
        tree = ast.parse(content, filename=file_path)
    except (SyntaxError, RecursionError, ValueError):
        return []

    bindings = _collect_os_bindings(tree, content)
    visitor = _EnvVarWithDefaultsVisitor(file_path, bindings)
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
