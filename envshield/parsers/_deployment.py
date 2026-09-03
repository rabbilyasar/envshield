# envshield/parsers/_deployment.py
# Best-effort content-sniffing to tell a docker-compose file apart from a
# Kubernetes manifest -- both are plain YAML, so extension alone can't do it.
import os
import re

import yaml

from ..core.exceptions import UnsafePathError
from ..utils.paths import is_within


def ensure_within_project(base_dir: str, relative_path: str, label: str) -> str:
    """
    Resolves `relative_path` against `base_dir` (e.g. a manifest's own
    directory, for a reference like 'env_file:' that's relative to the
    manifest, not the project root) and validates the result stays within
    the current project boundary. Raises UnsafePathError otherwise;
    returns the resolved (but not realpath'd) absolute path on success.

    The actual containment check (utils.paths.is_within) is shared with
    config/manager.py's own _ensure_within_project -- both need the exact
    same realpath-based, symlink-safe comparison (a symlink can satisfy a
    lexical containment check while its real target does not), and a
    review confirmed sharing it introduces no coupling either function
    didn't already have: utils/ is a pre-existing, dependency-free leaf
    package (see git_utils.py, already used by both config/manager.py and
    core/dependency_snapshot.py) that both this module and config/
    manager.py can depend on downward without depending on each other.
    This function keeps its own join semantics (relative to `base_dir`,
    not the project root) and its own return contract (the resolved
    absolute path, for immediate use -- unlike config/manager.py's
    version, which returns the original portable path for persistence
    into envshield.yml), since those genuinely differ between the two
    callers; only the shared boolean check itself was extracted. By the
    time any parser runs, the CLI has already chdir'd to the project root
    (see cli.py's startup sequence), which is the same assumption
    _ensure_within_project itself makes.

    A manifest is committed, untrusted content -- a 'env_file: ../../
    outside.env' reference could otherwise make EnvShield read an
    arbitrary file outside the project (BL-008).
    """
    candidate = os.path.join(base_dir, relative_path)
    project_root = os.path.abspath(os.getcwd())
    if not is_within(candidate, project_root):
        raise UnsafePathError(label, relative_path, project_root)
    return candidate


def detect_deployment_format(file_path: str) -> str | None:
    """
    Returns 'docker-compose' (top-level 'services:' mapping, no
    'apiVersion'), 'kubernetes' (any document in a possibly multi-document
    file has both 'apiVersion' and 'kind'), or None for anything else --
    including a file that isn't valid YAML at all.
    """
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r") as f:
            docs = [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]
    except yaml.YAMLError:
        return None

    if not docs:
        return None

    first = docs[0]
    if isinstance(first.get("services"), dict) and "apiVersion" not in first:
        return "docker-compose"
    if any("apiVersion" in d and "kind" in d for d in docs):
        return "kubernetes"
    return None


# A complete Go-template expression -- the specific syntax ('{{ ... }}')
# that breaks YAML parsing for the extremely common real-world case of
# checking a Helm chart template directly instead of its rendered output
# (e.g. 'value: {{ .Values.databaseUrl | quote }}'). Deliberately requires
# the full '{{...}}' pair, not a bare '{{' -- valid YAML essentially never
# contains a literal, unquoted '{{ ... }}' pair (an unquoted '{' alone
# already starts YAML's own flow-mapping syntax), so this doesn't fire on
# ordinary invalid YAML that's broken for an unrelated reason.
_HELM_TEMPLATE_EXPRESSION_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)


def safe_yaml_error_message(e: yaml.YAMLError) -> str:
    """
    yaml.YAMLError's own __str__ (and its Mark objects' own __str__, via
    problem_mark/context_mark) renders a code-context snippet that embeds
    the actual offending line's content -- verified directly: a secret-
    shaped string placed on a malformed line partially leaks into str(e).
    Only the error's structural attributes (problem/context descriptions,
    1-indexed line/column numbers) are safe to surface in a message a CLI
    might print or a JSON error field might carry -- this never touches a
    Mark's own __str__/get_snippet(), which is exactly what leaks content.
    """
    parts = []
    if getattr(e, "problem", None):
        parts.append(e.problem)
    mark = getattr(e, "problem_mark", None)
    if mark is not None:
        parts.append(f"at line {mark.line + 1}, column {mark.column + 1}")
    if getattr(e, "context", None):
        parts.append(f"({e.context})")
    return " ".join(parts) if parts else type(e).__name__


def looks_like_unrendered_helm_template(file_path: str) -> bool:
    """
    True when `file_path` contains at least one '{{ ... }}' Go-template
    expression. Intended for a caller that already knows detect_deployment_
    format() (or get_parser()) couldn't make sense of this file, to tell
    the common "this is a Helm template, not a renderable manifest" cause
    apart from genuinely invalid/unrelated YAML, so it can give a specific,
    actionable message instead of a generic one.
    """
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return False
    return bool(_HELM_TEMPLATE_EXPRESSION_RE.search(content))
