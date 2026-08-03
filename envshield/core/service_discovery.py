# envshield/core/service_discovery.py
# Heuristics for auto-detecting service-like directories in a monorepo, and
# how each one manages its environment variables.

import os
from typing import Dict, List, Optional

from . import inspector
from .scanner import DEFAULT_EXCLUDED_DIRS
from ..parsers.factory import get_parser

# Directories whose immediate children are conventionally one-service-per-
# subdirectory in a monorepo (Turborepo/Nx/Lerna-style), so their contents
# are scanned one level deeper than everything else.
SERVICE_CONTAINER_DIRNAMES = {"services", "apps", "packages"}

# Dotenv-style local files to look for directly inside a candidate
# directory, in preference order.
DOTENV_CANDIDATES = [".env", ".env.local", ".env.development", ".env.dev"]

# Python config modules to look for, in preference order. Checked only when
# no dotenv file was found -- a plain "config as code" module is the other
# common shape real projects use besides dotenv (e.g. Flask's
# `config/env_config.local.py`, checked straight into git).
PYTHON_CONFIG_CANDIDATES = [
    "config/env_config.local.py",
    "config/local_settings.py",
    "config/settings.py",
    "config/dev_settings.py",
    "settings/local.py",
    "settings.py",
    "config/config.py",
    "config.py",
]

# A python file needs at least this many top-level UPPER_CASE assignments to
# be treated as a plausible env-config module, rather than some unrelated
# script that happens to live at one of the candidate paths.
MIN_CONFIG_VARS = 3


def _looks_like_python_config_module(path: str) -> bool:
    parser = get_parser(path)
    if not parser:
        return False
    try:
        names = parser.get_vars(path)
    except (FileNotFoundError, OSError):
        return False
    upper_names = [n for n in names if n.isupper()]
    return len(upper_names) >= MIN_CONFIG_VARS


def detect_env_style(service_dir: str) -> Dict[str, Optional[str]]:
    """
    Looks inside `service_dir` for how it manages environment variables.

    Returns {"format": "dotenv" | "python" | None, "local_file": path or None}.
    `local_file` is only populated when it needs to differ from the default
    ('<service_dir>/.env') -- i.e. for the python format, or when the
    dotenv file found isn't literally named '.env'.
    """
    for name in DOTENV_CANDIDATES:
        candidate = os.path.join(service_dir, name)
        if os.path.isfile(candidate):
            return {
                "format": "dotenv",
                "local_file": None if name == ".env" else candidate,
            }

    for rel_path in PYTHON_CONFIG_CANDIDATES:
        candidate = os.path.join(service_dir, rel_path)
        if os.path.isfile(candidate) and _looks_like_python_config_module(candidate):
            return {"format": "python", "local_file": candidate}

    return {"format": None, "local_file": None}


def _candidate_dirs(root: str) -> List[str]:
    """Every directory worth checking: root's immediate children, plus one level into any 'services'/'apps'/'packages' folder."""
    if not os.path.isdir(root):
        return []

    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []

    dirs = []
    for entry in entries:
        full_path = os.path.join(root, entry)
        if (
            not os.path.isdir(full_path)
            or entry in DEFAULT_EXCLUDED_DIRS
            or entry.startswith(".")
        ):
            continue

        if entry in SERVICE_CONTAINER_DIRNAMES:
            try:
                nested_entries = sorted(os.listdir(full_path))
            except OSError:
                continue
            for nested_entry in nested_entries:
                nested_path = os.path.join(full_path, nested_entry)
                if (
                    os.path.isdir(nested_path)
                    and nested_entry not in DEFAULT_EXCLUDED_DIRS
                    and not nested_entry.startswith(".")
                ):
                    dirs.append(nested_path)
        else:
            dirs.append(full_path)

    return dirs


def discover_candidates(
    root: str = ".", known_dirs: Optional[List[str]] = None
) -> List[dict]:
    """
    Scans `root` for directories that look like independent services with
    their own environment configuration, skipping any directory that's
    already registered (by directory, not name) via `known_dirs`.

    A directory only qualifies if it has an actual environment-config
    signal -- a dotenv file, or a recognizable Python config module. A
    generic project marker alone (pyproject.toml, package.json, go.mod) is
    NOT enough by itself: a shared library package is exactly as likely to
    have one of those as a real service is, and has no env vars of its own.

    Returns a list of dicts: {name, dir, project_type, format, local_file}.
    """
    known_dirs_norm = {os.path.normpath(d) for d in (known_dirs or [])}
    candidates = []
    seen_names: Dict[str, int] = {}

    for service_dir in _candidate_dirs(root):
        normalized = os.path.normpath(service_dir)
        if normalized in known_dirs_norm:
            continue

        env_style = detect_env_style(normalized)
        if not env_style["format"]:
            continue

        base_name = os.path.basename(normalized)
        name = base_name
        if base_name in seen_names:
            # Disambiguate a repeated basename (e.g. two "api" dirs under
            # different parents) using its parent directory's name.
            parent = os.path.basename(os.path.dirname(normalized))
            name = f"{parent}-{base_name}" if parent else f"{base_name}-{seen_names[base_name]}"
        seen_names[base_name] = seen_names.get(base_name, 0) + 1

        candidates.append(
            {
                "name": name,
                "dir": normalized,
                "project_type": inspector.detect_project_type(service_dir),
                "format": env_style["format"],
                "local_file": env_style["local_file"],
            }
        )

    return candidates
