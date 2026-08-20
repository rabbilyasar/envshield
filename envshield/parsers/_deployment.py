# envshield/parsers/_deployment.py
# Best-effort content-sniffing to tell a docker-compose file apart from a
# Kubernetes manifest -- both are plain YAML, so extension alone can't do it.
import os
import re

import yaml


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
