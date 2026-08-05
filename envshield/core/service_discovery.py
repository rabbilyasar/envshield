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

# Dotenv filenames that are a checked-in *template* (blank/example values),
# not a real per-developer or per-deploy file. Real projects name these
# very differently (dotenv-rails' '.env.example', dotenvx's '.env.example',
# Rust/Go projects' '.env.dist', etc.) -- checked separately from, and only
# after, a real dotenv file, since a template's values aren't safe to treat
# as this service's actual current config.
DOTENV_TEMPLATE_NAMES = {".env.example", ".env.sample", ".env.template", ".env.dist"}

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

# Conventional docker-compose filenames, newest naming convention first
# (plain 'compose.yaml' is the current Compose Spec name; 'docker-compose.yml'
# is the long-standing, still far more common one in the wild).
COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def find_compose_file(service_dir: str, project_root: str = ".") -> Optional[str]:
    """
    Looks for a docker-compose file that plausibly configures `service_dir`:
    first directly inside it (a per-service compose file), then at the
    project root (the far more common shape -- one compose file listing
    every service). Kubernetes manifests aren't auto-detected here: unlike
    a compose file's single well-known name, there's no equally reliable
    convention for "which YAML file(s) under k8s/ belong to this service",
    so that stays an explicit '--deployment-manifest' opt-in for now.
    """
    for base_dir in (service_dir, project_root):
        for filename in COMPOSE_FILENAMES:
            candidate = os.path.join(base_dir, filename)
            if os.path.isfile(candidate):
                return os.path.normpath(candidate)
    return None


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


def _find_real_dotenv_file(service_dir: str) -> Optional[str]:
    """
    Finds the best real (non-template) dotenv-style file directly inside
    `service_dir`, preferring '.env' itself, then a '.local' variant, then
    any other '.env.*' file found.

    A short fixed list of names ('.env.development', '.env.dev', ...) badly
    undercounts what real projects actually commit: Rails/Mastodon's
    production convention is `.env.production`; Nx's per-target convention
    is `.env.<target>.<configuration>` (e.g. `.env.serve.development`);
    Vite/CRA add their own tiers on top. Matching any '.env.*' file (short
    of an outright template -- see DOTENV_TEMPLATE_NAMES) covers all of
    these without needing to enumerate every framework's naming scheme.
    """
    exact = os.path.join(service_dir, ".env")
    if os.path.isfile(exact):
        return exact

    try:
        entries = sorted(os.listdir(service_dir))
    except OSError:
        return None

    candidates = [
        os.path.join(service_dir, name)
        for name in entries
        if name.startswith(".env.")
        and name not in DOTENV_TEMPLATE_NAMES
        and os.path.isfile(os.path.join(service_dir, name))
    ]
    for candidate in candidates:
        if candidate.endswith(".local"):
            return candidate
    return candidates[0] if candidates else None


def _find_dotenv_template(service_dir: str) -> Optional[str]:
    """Finds a checked-in dotenv template, for when no real local file exists yet."""
    for name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        candidate = os.path.join(service_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def detect_env_style(service_dir: str) -> Dict[str, Optional[str]]:
    """
    Looks inside `service_dir` for how it manages environment variables.

    Returns {"format": "dotenv" | "python" | None, "local_file": path or
    None, "example_file": path or None}. `local_file`/`example_file` are
    only populated when they need to differ from the defaults
    ('<service_dir>/.env' / '<service_dir>/.env.example') -- i.e. for the
    python format, when the real dotenv file found isn't literally named
    '.env', or when only a template was found under a non-standard name.
    """
    real_file = _find_real_dotenv_file(service_dir)
    if real_file:
        return {
            "format": "dotenv",
            "local_file": None if os.path.basename(real_file) == ".env" else real_file,
            "example_file": None,
        }

    template_file = _find_dotenv_template(service_dir)
    if template_file:
        return {
            "format": "dotenv",
            "local_file": None,
            "example_file": (
                None if os.path.basename(template_file) == ".env.example" else template_file
            ),
        }

    for rel_path in PYTHON_CONFIG_CANDIDATES:
        candidate = os.path.join(service_dir, rel_path)
        if os.path.isfile(candidate) and _looks_like_python_config_module(candidate):
            return {"format": "python", "local_file": candidate, "example_file": None}

    return {"format": None, "local_file": None, "example_file": None}


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

    Returns a list of dicts: {name, dir, project_type, format, local_file,
    example_file, deployment_manifest}. `deployment_manifest`, when found,
    is registered automatically -- no '--deployment-manifest' flag needed
    for the common case of a docker-compose file at the project root or
    inside the service's own directory (see find_compose_file). If its
    compose file lists more than one service, `check`/`doctor` still work
    without any extra setup as long as this service's name matches one of
    the compose service names (see the parsers' `prefer` hint) -- otherwise
    they'll report exactly which flag/edit resolves the ambiguity.
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
                "example_file": env_style["example_file"],
                "deployment_manifest": find_compose_file(normalized, root),
            }
        )

    return candidates
