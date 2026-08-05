# envshield/parsers/_deployment.py
# Best-effort content-sniffing to tell a docker-compose file apart from a
# Kubernetes manifest -- both are plain YAML, so extension alone can't do it.
import os

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
