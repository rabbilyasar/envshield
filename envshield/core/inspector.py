# envshield/core/inspector.py
# Logic for inspecting a project to determine its type.
import json
import os
from typing import List, Optional

import toml


def _find_framework_in_list(dependencies: List[str]) -> Optional[str]:
    """Helper to find a known framework in a list of Python dependencies."""
    for dep in dependencies:
        dep_lower = dep.lower()
        if "django" in dep_lower:
            return "python-django"
        if "flask" in dep_lower:
            return "python-flask"
    return None


def _check_pyproject_toml(directory: str) -> Optional[str]:
    """Parses pyproject.toml to find a framework."""
    path = os.path.join(directory, "pyproject.toml")
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            data = toml.load(f)

        dependencies = data.get("project", {}).get("dependencies", [])
        framework = _find_framework_in_list(dependencies)
        if framework:
            return framework

        optional_deps = data.get("project", {}).get("optional-dependencies", {})
        for group in optional_deps.values():
            framework = _find_framework_in_list(group)
            if framework:
                return framework

    except (IOError, toml.TomlDecodeError):
        pass

    return "python"


def _check_requirements_txt(directory: str) -> Optional[str]:
    """Parses requirements.txt to find a framework."""
    path = os.path.join(directory, "requirements.txt")
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r") as f:
            dependencies = f.readlines()

        framework = _find_framework_in_list(dependencies)
        if framework:
            return framework
    except IOError:
        pass

    return "python"


_COUNT_LIMIT = 20000
_PY = (".py",)
_JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")


def _count_sources(directory: str) -> tuple:
    """(Python files, JS/TS files) under `directory`, up to a limit (BL-180)."""
    from .source_files import DEFAULT_EXCLUDED_DIRS

    py = js = seen = 0
    for _root, dirs, files in os.walk(directory):
        dirs[:] = [
            d for d in dirs if d not in DEFAULT_EXCLUDED_DIRS and not d.startswith(".")
        ]
        for name in files:
            seen += 1
            if name.endswith(_PY):
                py += 1
            elif name.endswith(_JS):
                js += 1
        if seen >= _COUNT_LIMIT:
            break
    return py, js


def _python_type(directory: str) -> Optional[str]:
    return _check_pyproject_toml(directory) or _check_requirements_txt(directory)


def detect_project_type(directory: str = ".") -> Optional[str]:
    """
    Detects the project type by looking for marker files and their content.

    `directory` defaults to the current directory (EnvShield's original,
    single-project behavior), but can be any path -- used by service
    discovery to identify each candidate service's own framework from its
    own directory, rather than the repo root's.
    """
    # A package.json beside Python markers (a frontend build, tooling) doesn't
    # make a mostly-Python project a Node one: the larger code base wins
    # (BL-180: 622 .py files and 49 JS/TS were reported as "nodejs").
    package_json_path = os.path.join(directory, "package.json")
    if os.path.exists(package_json_path) and (
        os.path.exists(os.path.join(directory, "pyproject.toml"))
        or os.path.exists(os.path.join(directory, "requirements.txt"))
    ):
        py, js = _count_sources(directory)
        if py > js:
            return _python_type(directory)

    # Check for Node.js projects
    if os.path.exists(package_json_path):
        try:
            with open(package_json_path, "r") as f:
                lines = [line for line in f if not line.strip().startswith("//")]
                content = "".join(lines)
                data = json.loads(content)
            dependencies = data.get("dependencies", {})
            devDependencies = data.get("devDependencies", {})

            if "next" in dependencies or "next" in devDependencies:
                return "nextjs"
            if "vite" in dependencies or "vite" in devDependencies:
                return "vite"
            return "nodejs"
        except (IOError, json.JSONDecodeError):
            return "nodejs"

    # Check for Python projects with specific frameworks
    framework = _check_pyproject_toml(directory)
    if framework:
        return framework

    framework = _check_requirements_txt(directory)
    if framework:
        return framework

    # Check for Go projects
    if os.path.exists(os.path.join(directory, "go.mod")):
        return "go"

    return None
